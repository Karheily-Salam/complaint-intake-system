"""Emerging-incident detection: bursts of complaints about the same thing.

One outage, say a payment provider going down, produces many complaints within
hours, from different customers, in different words and languages, and each
agent only sees their own ticket.

Method, on the stored complaint embeddings (:mod:`app.ml.embeddings`):

1. **Cluster the recent window.** Complaints created in the last
   ``window_hours`` are grouped by average-linkage agglomerative clustering:
   two groups merge while their mean pairwise cosine similarity stays above
   the embedder's ``cluster_threshold``. Deterministic, no preset number of
   clusters, and exact at this scale (hundreds of complaints).
2. **Compare each cluster with its own history.** For a cluster's centroid,
   count how many complaints in the preceding ``baseline_days`` were just as
   close to it, and scale that to the window length - the expected count if
   nothing unusual were happening.
3. **Test for a burst.** Under a Poisson model with that expected rate, how
   likely is a count at least as large as the one observed? A cluster is
   reported only when that tail probability is below ``alpha``, the cluster
   has at least ``min_size`` complaints, and it is at least ``min_ratio``
   times its expected size. A topic that is always busy is therefore not an
   incident; a quiet topic that suddenly is, is.
4. **Describe it.** The cluster's most distinctive words (class-based TF-IDF
   over identifier-masked text), its complaint types and languages, and the
   tickets in it.

Separately, a plain time-series check flags a **volume spike** per complaint
type (window count against the type's baseline rate, same Poisson test) -
useful when the complaints about an incident are too varied to cluster.

Everything here is advisory: nothing touches a ticket or sends anything.
"""

from __future__ import annotations

import hashlib
import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.models.complaint import Complaint
from app.db.models.conversation import Conversation
from app.ml.embedding_store import complaint_text, embed_pending, load_vectors
from app.ml.embeddings import Embedder, get_embedder
from app.ml.features import tokenize

# Words that carry no topic in any of the three languages, plus the masking
# placeholders. Kept short on purpose: c-TF-IDF already discounts words that
# are common everywhere; this only removes the ones that would still win.
_STOPWORDS_RAW = (
    """
    the and for but not with have has had was were are you your our they this that from
    into after before since still been will can could would please just about there what
    when why how its it's i'm dont didnt cant my me we us an a to of in on at is it be
    email id number url
    и в во не на что как это но по за из от до у же ли бы то уже еще ещё мне меня мой моя
    мои мы вы вас ваш они он она его её их был была было были есть нет так там тут для
    при через после пожалуйста
    في من على الى إلى عن مع هذا هذه ذلك التي الذي لم لا ما ان أن كان كانت قد ثم او أو
    لكن منذ حتى عند بعد قبل انا أنا لي لدي هل تم يتم
    """
)
# Normalised the same way as the text they are compared with (Arabic letter
# variants folded, and so on), so "إلى" in the list also removes "الى".
_STOPWORDS = frozenset(tokenize(_STOPWORDS_RAW))

_MAX_RECENT = 500
_EMBED_BATCH = 256
_MAX_EMBED_BATCHES = 40
_MAX_REFERENCES = 10


@dataclass
class Incident:
    id: str
    is_demo: bool
    size: int
    expected: float
    ratio: float
    p_value: float
    severity: str
    first_seen: datetime
    last_seen: datetime
    top_terms: list[str]
    types: dict[str, int]
    languages: dict[str, int]
    ticket_references: list[str]
    open_complaints: int
    cohesion: float
    hourly_counts: list[int] = field(default_factory=list)


@dataclass
class VolumeSpike:
    is_demo: bool
    complaint_type: str
    count: int
    expected: float
    ratio: float
    p_value: float


@dataclass
class IncidentReport:
    generated_at: datetime
    window_hours: int
    baseline_days: int
    model_version: str
    cluster_threshold: float
    complaints_in_window: int
    incidents: list[Incident]
    volume_spikes: list[VolumeSpike]


# ------------------------------------------------------------------ statistics


def poisson_sf(k: int, lam: float) -> float:
    """P(X >= k) for X ~ Poisson(lam), computed stably for small counts."""
    if k <= 0:
        return 1.0
    lam = max(lam, 1e-12)
    log_pmf = -lam
    cdf = math.exp(log_pmf)
    for i in range(1, k):
        log_pmf += math.log(lam) - math.log(i)
        cdf += math.exp(log_pmf)
    return max(0.0, 1.0 - cdf)


def average_linkage_clusters(matrix: np.ndarray, threshold: float) -> list[list[int]]:
    """Agglomerative clustering with average linkage on cosine similarity.

    Rows of ``matrix`` are L2-normalised. Merges the most similar pair of
    clusters while their average pairwise similarity is >= ``threshold``
    (Lance-Williams update, so each merge is O(n)). Returns lists of row
    indices, largest cluster first.
    """
    n = matrix.shape[0]
    if n == 0:
        return []
    sim = (matrix @ matrix.T).astype(np.float64)
    np.fill_diagonal(sim, -np.inf)
    sizes = np.ones(n)
    members: dict[int, list[int]] = {i: [i] for i in range(n)}
    active = np.ones(n, dtype=bool)
    while True:
        masked = np.where(active[:, None] & active[None, :], sim, -np.inf)
        flat = int(np.argmax(masked))
        a, b = divmod(flat, n)
        if not np.isfinite(masked[a, b]) or masked[a, b] < threshold:
            break
        total = sizes[a] + sizes[b]
        merged = (sizes[a] * sim[a] + sizes[b] * sim[b]) / total
        sim[a, :] = merged
        sim[:, a] = merged
        sim[a, a] = -np.inf
        sim[b, :] = -np.inf
        sim[:, b] = -np.inf
        sizes[a] = total
        active[b] = False
        members[a].extend(members.pop(b))
    clusters = [members[i] for i in range(n) if active[i]]
    return sorted(clusters, key=len, reverse=True)


def distinctive_terms(
    cluster_texts: list[str], all_texts: list[str], limit: int = 5
) -> list[str]:
    """Class-based TF-IDF: frequent in the cluster, rare elsewhere."""
    df: Counter[str] = Counter()
    for text in all_texts:
        df.update(set(tokenize(text)))
    tf: Counter[str] = Counter()
    for text in cluster_texts:
        tf.update(tokenize(text))
    n_docs = max(len(all_texts), 1)
    scored = [
        (count * math.log(1 + n_docs / df[word]), word)
        for word, count in tf.items()
        if len(word) >= 3 and word not in _STOPWORDS and not word.isdigit() and df[word]
    ]
    scored.sort(key=lambda s: (-s[0], s[1]))
    return [word for _, word in scored[:limit]]


# ------------------------------------------------------------------ detection


def detect_incidents(
    db: Session,
    *,
    window_hours: int = 6,
    baseline_days: int = 14,
    min_size: int = 3,
    min_ratio: float = 3.0,
    alpha: float = 0.01,
    now: datetime | None = None,
    embedder: Embedder | None = None,
) -> IncidentReport:
    embedder = embedder or get_embedder()
    now = _naive_utc(now or datetime.now(UTC))
    # Catch up on unembedded complaints first - a history with gaps would
    # understate the baseline and make ordinary traffic look like a burst.
    for _ in range(_MAX_EMBED_BATCHES):
        if embed_pending(db, embedder, limit=_EMBED_BATCH) < _EMBED_BATCH:
            break

    window_start = now - timedelta(hours=window_hours)
    baseline_start = window_start - timedelta(days=baseline_days)
    window_fraction = window_hours / (baseline_days * 24)

    incidents: list[Incident] = []
    spikes: list[VolumeSpike] = []
    in_window = 0
    for is_demo in (False, True):
        vectors = load_vectors(db, is_demo=is_demo, embedder=embedder, since=baseline_start)
        if not vectors.complaint_ids:
            continue
        created = [_naive_utc(c) for c in vectors.created_at]
        recent_idx = [i for i, c in enumerate(created) if c >= window_start][-_MAX_RECENT:]
        base_idx = [i for i, c in enumerate(created) if c < window_start]
        in_window += len(recent_idx)
        if not recent_idx:
            continue

        complaints = _load_complaints(db, vectors.complaint_ids)
        recent = vectors.matrix[recent_idx]
        baseline = vectors.matrix[base_idx] if base_idx else np.zeros((0, recent.shape[1]))
        texts = {cid: complaint_text(complaints[cid]) for cid in vectors.complaint_ids}

        for cluster in average_linkage_clusters(recent, embedder.cluster_threshold):
            if len(cluster) < min_size:
                continue
            rows = recent[cluster]
            centroid = rows.mean(axis=0)
            centroid /= max(float(np.linalg.norm(centroid)), 1e-12)
            baseline_hits = (
                int((baseline @ centroid >= embedder.cluster_threshold).sum())
                if len(baseline) else 0
            )
            # A floor on the expected rate: an empty history must not make a
            # handful of complaints look infinitely surprising.
            expected = max(baseline_hits * window_fraction, 0.5)
            p_value = poisson_sf(len(cluster), expected)
            ratio = len(cluster) / expected
            if p_value >= alpha or ratio < min_ratio:
                continue

            ids = [vectors.complaint_ids[recent_idx[i]] for i in cluster]
            members = [complaints[cid] for cid in ids]
            stamps = [created[recent_idx[i]] for i in cluster]
            pairwise = rows @ rows.T
            cohesion = float(
                (pairwise.sum() - len(cluster)) / max(len(cluster) * (len(cluster) - 1), 1)
            )
            incidents.append(
                Incident(
                    id="inc-" + hashlib.sha1(
                        ",".join(map(str, sorted(ids)[:3])).encode()
                    ).hexdigest()[:10],
                    is_demo=is_demo,
                    size=len(cluster),
                    expected=round(expected, 3),
                    ratio=round(ratio, 2),
                    p_value=float(f"{p_value:.3g}"),
                    severity="high" if (len(cluster) >= 10 or p_value < 1e-4) else "medium",
                    first_seen=min(stamps),
                    last_seen=max(stamps),
                    top_terms=distinctive_terms(
                        [texts[cid] for cid in ids], list(texts.values())
                    ),
                    types=dict(Counter(c.type or "unclassified" for c in members)),
                    languages=dict(
                        Counter(c.conversation.language_code or "unknown" for c in members)
                    ),
                    ticket_references=sorted(
                        c.ticket.reference for c in members if c.ticket is not None
                    )[:_MAX_REFERENCES],
                    open_complaints=sum(1 for c in members if c.ticket is None),
                    cohesion=round(cohesion, 3),
                    hourly_counts=_hourly(stamps, window_start, window_hours),
                )
            )

        spikes += _volume_spikes(
            db, is_demo, window_start, baseline_start, window_fraction, min_size, alpha,
            min_ratio,
        )

    incidents.sort(key=lambda i: (i.p_value, -i.size))
    return IncidentReport(
        generated_at=now,
        window_hours=window_hours,
        baseline_days=baseline_days,
        model_version=embedder.version,
        cluster_threshold=embedder.cluster_threshold,
        complaints_in_window=in_window,
        incidents=incidents,
        volume_spikes=spikes,
    )


def _volume_spikes(
    db: Session,
    is_demo: bool,
    window_start: datetime,
    baseline_start: datetime,
    window_fraction: float,
    min_size: int,
    alpha: float,
    min_ratio: float,
) -> list[VolumeSpike]:
    rows = db.execute(
        select(Complaint.type, Complaint.created_at)
        .join(Conversation, Conversation.id == Complaint.conversation_id)
        .where(
            Conversation.is_demo.is_(is_demo),
            Complaint.type.is_not(None),
            Complaint.created_at >= baseline_start,
        )
    ).all()
    window: Counter[str] = Counter()
    history: Counter[str] = Counter()
    for complaint_type, created in rows:
        (window if _naive_utc(created) >= window_start else history)[complaint_type] += 1
    spikes = []
    for complaint_type, count in window.items():
        expected = max(history[complaint_type] * window_fraction, 0.5)
        p_value = poisson_sf(count, expected)
        if count >= min_size and p_value < alpha and count / expected >= min_ratio:
            spikes.append(
                VolumeSpike(
                    is_demo=is_demo,
                    complaint_type=complaint_type,
                    count=count,
                    expected=round(expected, 3),
                    ratio=round(count / expected, 2),
                    p_value=float(f"{p_value:.3g}"),
                )
            )
    return spikes


def _load_complaints(db: Session, ids: list[int]) -> dict[int, Complaint]:
    rows = db.scalars(
        select(Complaint)
        .where(Complaint.id.in_(ids))
        .options(
            selectinload(Complaint.ticket),
            selectinload(Complaint.conversation).selectinload(Conversation.messages),
        )
    )
    return {c.id: c for c in rows}


def _hourly(stamps: list[datetime], start: datetime, hours: int) -> list[int]:
    counts = [0] * hours
    for stamp in stamps:
        bucket = int((stamp - start).total_seconds() // 3600)
        counts[min(max(bucket, 0), hours - 1)] += 1
    return counts


def _naive_utc(value: datetime) -> datetime:
    """SQLite hands back naive UTC timestamps; compare everything that way."""
    if value.tzinfo is not None:
        value = value.astimezone(UTC).replace(tzinfo=None)
    return value
