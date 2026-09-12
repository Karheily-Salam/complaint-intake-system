"""Embeddings: identifier masking, both backends, storage and the worker.

The ONNX tests run only where the model has been downloaded
(scripts/download_embedding_model.py) and onnxruntime is installed; CI runs
the hashing backend, which is the default.
"""

from __future__ import annotations

import importlib.util

import numpy as np
import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.db.models.complaint import Complaint
from app.db.models.complaint_embedding import ComplaintEmbedding
from app.ml import embeddings as embeddings_module
from app.ml.embedding_store import complaint_text, embed_pending, ensure_vector, load_vectors
from app.ml.embeddings import (
    DEFAULT_ONNX_DIR,
    HashingEmbedder,
    OnnxE5Embedder,
    get_embedder,
    onnx_model_available,
)
from app.ml.pii import mask_identifiers
from app.services import ml_worker

# ------------------------------------------------------------------ masking


def test_identifiers_are_masked_and_words_kept():
    text = (
        "user U-482913, email jane.doe@example.com, card 4111 1111 1111 1111, "
        "txn TXN-9f3a12bc, id 583921, see https://example.com/x - I paid 100 dollars"
    )
    masked = mask_identifiers(text)
    for secret in ("482913", "jane.doe", "4111", "9f3a12bc", "583921", "example.com/x"):
        assert secret not in masked
    assert "100 dollars" in masked and "I paid" in masked
    assert "[email]" in masked and "[id]" in masked


def test_masking_leaves_ordinary_text_alone():
    assert mask_identifiers("Мой вывод не пришёл 5 дней") == "Мой вывод не пришёл 5 дней"


# ------------------------------------------------------------------ hashing backend


def test_hashing_vectors_are_normalised_and_deterministic():
    emb = HashingEmbedder()
    first = emb.embed(["My withdrawal is stuck", ""])
    second = emb.embed(["My withdrawal is stuck", ""])
    assert first.shape == (2, emb.dim)
    assert np.isclose(np.linalg.norm(first[0]), 1.0)
    assert not first[1].any(), "empty text is a zero vector, never a random one"
    assert np.array_equal(first, second)


def test_hashing_separates_near_duplicates_from_unrelated_text():
    emb = HashingEmbedder()
    a, b, c = emb.embed(
        [
            "My withdrawal to my Visa card has not arrived for five days",
            "My withdrawal to my visa card still has not arrived after five days",
            "I cannot log in because the app crashes on start",
        ]
    )
    assert float(a @ b) >= emb.duplicate_threshold
    assert float(a @ c) < emb.similar_threshold


# ------------------------------------------------------------------ configuration


def test_default_backend_is_hashing():
    assert isinstance(get_embedder(), HashingEmbedder)


def test_a_missing_onnx_model_falls_back_to_hashing(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "embedding_provider", "onnx")
    monkeypatch.setattr(settings, "embedding_model_dir", str(tmp_path))
    get_embedder.cache_clear()
    try:
        assert isinstance(get_embedder(), HashingEmbedder)
    finally:
        get_embedder.cache_clear()


def test_an_unknown_backend_is_rejected(monkeypatch):
    monkeypatch.setattr(settings, "embedding_provider", "pinecone")
    get_embedder.cache_clear()
    try:
        with pytest.raises(ValueError):
            get_embedder()
    finally:
        get_embedder.cache_clear()


# ------------------------------------------------------------------ storage


def make_complaints(client, bodies, sender="emb@example.com"):
    for body in bodies:
        response = client.post("/api/v1/inbox", json={"from_addr": sender, "body": body})
        assert response.status_code == 201


def test_complaint_text_is_masked(client, db_session):
    make_complaints(client, ["My withdrawal is stuck, user id U-482913, email a.b@example.com"])
    complaint = db_session.scalar(select(Complaint))
    text = complaint_text(complaint)
    assert "withdrawal" in text and "482913" not in text and "a.b@example.com" not in text


def test_embed_pending_is_incremental(client, db_session):
    make_complaints(client, ["My withdrawal is stuck", "My deposit never arrived"])
    assert embed_pending(db_session) == 2
    db_session.commit()
    assert embed_pending(db_session) == 0, "nothing changed, nothing recomputed"
    rows = db_session.scalars(select(ComplaintEmbedding)).all()
    assert {r.model_version for r in rows} == {HashingEmbedder().version}
    assert all(r.dim == 1024 and len(r.vector) == 1024 * 4 for r in rows)


def test_a_changed_description_is_re_embedded(client, db_session):
    make_complaints(client, ["My withdrawal is stuck"])
    embed_pending(db_session)
    db_session.commit()
    complaint = db_session.scalar(select(Complaint))
    before = db_session.scalar(select(ComplaintEmbedding)).text_sha256
    complaint.concise_description = "A completely different description of a login problem"
    db_session.commit()
    vector = ensure_vector(db_session, complaint)
    db_session.commit()
    after = db_session.scalar(select(ComplaintEmbedding)).text_sha256
    assert vector is not None and after != before


def test_vectors_of_another_model_version_are_ignored(client, db_session):
    make_complaints(client, ["My withdrawal is stuck"])
    complaint = db_session.scalar(select(Complaint))
    db_session.add(
        ComplaintEmbedding(
            complaint_id=complaint.id,
            model_name="old",
            model_version="old-model-v0",
            dim=3,
            vector=np.ones(3, dtype=np.float32).tobytes(),
            text_sha256="x",
        )
    )
    db_session.commit()
    assert load_vectors(db_session, is_demo=True).matrix.shape == (0, 1024)
    embed_pending(db_session)
    db_session.commit()
    assert load_vectors(db_session, is_demo=True).matrix.shape == (1, 1024)


def test_vectors_are_scoped_to_demo_or_real(client, db_session):
    make_complaints(client, ["My withdrawal is stuck"])
    embed_pending(db_session)
    db_session.commit()
    assert len(load_vectors(db_session, is_demo=True).complaint_ids) == 1
    assert load_vectors(db_session, is_demo=False).complaint_ids == []


def test_the_worker_embeds_in_its_own_session(client, engine):
    make_complaints(client, ["My deposit never arrived", "My withdrawal is stuck"])
    factory = sessionmaker(bind=engine, autoflush=False)
    assert ml_worker.run_once(factory) == 2
    assert ml_worker.run_once(factory) == 0
    assert ml_worker.ml_worker_health.last_run_at is not None


# ------------------------------------------------------------------ ONNX backend

onnx_ready = pytest.mark.skipif(
    not onnx_model_available(DEFAULT_ONNX_DIR)
    or not all(importlib.util.find_spec(m) for m in ("onnxruntime", "tokenizers")),
    reason="multilingual ONNX model not downloaded (scripts/download_embedding_model.py)",
)


@pytest.fixture(scope="module")
def e5():
    return OnnxE5Embedder(DEFAULT_ONNX_DIR)


@onnx_ready
def test_onnx_vectors_are_384d_and_normalised(e5):
    vectors = e5.embed(["hello", "مرحبا"])
    assert vectors.shape == (2, 384)
    assert np.allclose(np.linalg.norm(vectors, axis=1), 1.0, atol=1e-4)
    assert e5.version.startswith("multilingual-e5-small-int8+")


@onnx_ready
@pytest.mark.parametrize(
    "translation",
    [
        "Вывод на банковскую карту не пришёл уже пять дней",
        "لم يصل السحب إلى بطاقتي البنكية منذ خمسة أيام",
    ],
)
def test_onnx_places_translations_together(e5, translation):
    english = "My withdrawal to my bank card has not arrived for five days"
    unrelated = "I cannot log in because the app crashes on start"
    en, tr, un = e5.embed([english, translation, unrelated])
    assert float(en @ tr) >= e5.similar_threshold
    assert float(en @ tr) > float(en @ un)


@onnx_ready
def test_onnx_bounds_its_own_batch_size(e5):
    """A caller asking for 256 texts must not become a 256-wide forward pass.

    The session already costs ~400 MB resident; activations scale with batch
    size, and an unbounded batch measured over 1 GB - more than the host has
    to spare. Chunking here bounds it wherever the call comes from (the worker
    asks for 64, incident detection for 256).
    """
    assert e5.MAX_BATCH <= 16
    texts = [f"complaint number {i} about a withdrawal that never arrived" for i in range(20)]
    chunked = e5.embed(texts)
    assert chunked.shape == (20, e5.dim)
    assert np.allclose(np.linalg.norm(chunked, axis=1), 1.0, atol=1e-4)

    # Chunking must not change what a vector *means*. It is deliberately not
    # asserted bit-for-bit: with int8 kernels the batch shape (and the padding
    # width that comes with it) changes rounding slightly. Measured agreement
    # is ~0.995 cosine, far inside the margins the thresholds work at.
    piecewise = np.vstack([e5.embed(texts[:7]), e5.embed(texts[7:])])
    assert float(np.min(np.sum(chunked * piecewise, axis=1))) >= 0.99


def test_embedder_module_exposes_one_factory():
    assert embeddings_module.get_embedder is get_embedder
