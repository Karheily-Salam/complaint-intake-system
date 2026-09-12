"""Multilingual text embeddings, shared by similarity search and incident detection.

One embedding per complaint, computed once and stored, then reused by every
feature that needs "how alike are these two complaints?". Two backends sit
behind one interface:

``onnx`` - **intfloat/multilingual-e5-small** (MIT licence), int8-quantised,
    run with ONNX Runtime on the CPU. A real multilingual sentence encoder: a
    Russian and an Arabic description of the same problem land close together.
    ~118 MB on disk, 384 dimensions, tens of milliseconds per complaint on one
    core. Needs the optional ``ml-onnx`` extra and the model files fetched by
    ``scripts/download_embedding_model.py``.

``hashing`` - character n-gram feature hashing (:mod:`app.ml.features`),
    numpy only, no download. Similar wording in the *same* language scores
    high; it does not know that "вывод" and "withdrawal" mean the same thing.
    The default, and the automatic fallback when the ONNX model is missing, so
    the feature works everywhere - tests, CI, a fresh clone - without it.

Vectors are stored per ``version``: two backends (or two model files) never
share an embedding space, so comparisons only ever use vectors of the active
version, and switching backend simply re-embeds in the background.

Each backend carries its own similarity thresholds, because cosine scores are
not comparable across models: e5 scores cluster high, hashing scores low.
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from functools import lru_cache
from pathlib import Path

import numpy as np

from app.core.config import BACKEND_DIR, settings
from app.core.logging import get_logger
from app.ml.features import HashingVectorizer

logger = get_logger(__name__)

DEFAULT_ONNX_DIR = BACKEND_DIR / "models" / "multilingual-e5-small"


class Embedder(ABC):
    name: str
    version: str
    dim: int
    # Cosine similarity at or above which two complaints are shown as
    # "similar", and at or above which - together with the identity/field
    # checks in app.ml.similarity - they may be flagged as a possible
    # duplicate. Calibrated per backend; see tests/test_embeddings.py.
    similar_threshold: float
    duplicate_threshold: float
    # Complaints closer than this are merged into one cluster by incident
    # detection.
    cluster_threshold: float

    @abstractmethod
    def embed(self, texts: list[str]) -> np.ndarray:
        """(len(texts), dim) float32, each row L2-normalised (zero if empty)."""


class HashingEmbedder(Embedder):
    name = "hashing-char-ngram"
    dim = 1024
    # Calibrated on the dataset: the 99th percentile of cosine between
    # complaints of *different* types is 0.28, so 0.30 means "shares real
    # wording", and 0.50 is near-verbatim overlap.
    similar_threshold = 0.30
    duplicate_threshold = 0.50
    cluster_threshold = 0.30

    def __init__(self) -> None:
        self._vectorizer = HashingVectorizer(dim=self.dim, char_ngrams=(3, 5))
        self.version = f"{self.name}-{self.dim}-v1"

    def embed(self, texts: list[str]) -> np.ndarray:
        return self._vectorizer.transform(texts)


class OnnxE5Embedder(Embedder):
    name = "multilingual-e5-small-int8"
    dim = 384
    # Calibrated on the dataset: the same complaint written in two languages
    # scores 0.917 (median), complaints of different types 0.902 at the 95th
    # and 0.922 at the 99th percentile. e5 scores are compressed into a high
    # band, which is why these look high next to the hashing thresholds.
    similar_threshold = 0.90
    duplicate_threshold = 0.94
    cluster_threshold = 0.90

    MODEL_FILE = "model_qint8_avx512_vnni.onnx"
    # Texts per forward pass. The session itself costs roughly 400 MB resident;
    # activations are what a caller can blow up, and they scale with batch x
    # sequence length. Measured: 8 texts of 256 tokens adds ~60 MB, 64 adds
    # ~460 MB. Callers ask for whatever they have to embed (the worker batches
    # 64, incident detection 256), so the bound belongs here, not in them.
    MAX_BATCH = 8
    # e5 was trained with an instruction prefix; "query: " is the one its
    # authors recommend for symmetric tasks such as similarity and clustering.
    PREFIX = "query: "

    def __init__(self, model_dir: Path, *, max_length: int = 256, threads: int = 1) -> None:
        import onnxruntime as ort
        from tokenizers import Tokenizer

        model_path = model_dir / self.MODEL_FILE
        self._tokenizer = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
        pad_id = self._tokenizer.token_to_id("<pad>")
        self._tokenizer.enable_truncation(max_length=max_length)
        self._tokenizer.enable_padding(
            pad_id=pad_id if pad_id is not None else 1, pad_token="<pad>"
        )

        options = ort.SessionOptions()
        # The production host has one vCPU shared with the API and the mail
        # poller; more threads would only contend with them.
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
        # Return transient allocations to the OS instead of holding an arena:
        # this runs next to the API and the mail poller on a 1.9 GB host, where
        # steady resident size matters more than the last few ms of throughput.
        options.enable_cpu_mem_arena = False
        self._session = ort.InferenceSession(
            str(model_path), sess_options=options, providers=["CPUExecutionProvider"]
        )
        self._inputs = {i.name for i in self._session.get_inputs()}
        digest = hashlib.sha256(model_path.read_bytes()).hexdigest()
        self.version = f"{self.name}+{digest[:8]}"

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        if len(texts) > self.MAX_BATCH:
            return np.vstack(
                [
                    self.embed(texts[i : i + self.MAX_BATCH])
                    for i in range(0, len(texts), self.MAX_BATCH)
                ]
            )
        encodings = self._tokenizer.encode_batch([self.PREFIX + (t or "") for t in texts])
        ids = np.array([e.ids for e in encodings], dtype=np.int64)
        mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)
        feeds = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in self._inputs:
            feeds["token_type_ids"] = np.zeros_like(ids)
        hidden = self._session.run(None, feeds)[0]  # (batch, seq, dim)
        weights = mask[..., None].astype(np.float32)
        pooled = (hidden * weights).sum(axis=1) / np.clip(weights.sum(axis=1), 1e-9, None)
        norms = np.linalg.norm(pooled, axis=1, keepdims=True)
        return (pooled / np.clip(norms, 1e-12, None)).astype(np.float32)


def onnx_model_available(model_dir: Path) -> bool:
    return (model_dir / OnnxE5Embedder.MODEL_FILE).is_file() and (
        model_dir / "tokenizer.json"
    ).is_file()


@lru_cache
def get_embedder() -> Embedder:
    """The configured backend, falling back to hashing if ONNX cannot load.

    A missing model degrades the quality of similar-ticket suggestions; it
    must never stop the service. The fallback is logged loudly and reported
    by the ML monitoring endpoint, so it is visible rather than silent.
    """
    backend = settings.embedding_provider.lower()
    if backend == "onnx":
        model_dir = Path(settings.embedding_model_dir or DEFAULT_ONNX_DIR)
        if onnx_model_available(model_dir):
            try:
                return OnnxE5Embedder(model_dir)
            except Exception:
                logger.exception("ONNX embedding model failed to load; using hashing embedder")
        else:
            logger.error(
                "EMBEDDING_PROVIDER=onnx but no model in %s - run "
                "scripts/download_embedding_model.py. Using the hashing embedder.",
                model_dir,
            )
    elif backend != "hashing":
        raise ValueError(f"Unknown EMBEDDING_PROVIDER '{backend}'. Expected 'hashing' or 'onnx'.")
    return HashingEmbedder()


def cosine_matrix(query: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    """Cosine similarity of one L2-normalised vector against many."""
    if matrix.size == 0:
        return np.zeros(0, dtype=np.float32)
    return matrix @ query
