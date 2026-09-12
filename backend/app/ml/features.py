"""Deterministic text features shared by the classical ML models.

Feature hashing over character n-grams plus word unigrams and bigrams:

* **Script-agnostic.** Character n-grams work the same for English, Russian
  and Arabic. Russian inflection ("вывод", "вывода", "выводом") and Arabic
  clitics ("السحب", "بالسحب") share most of their n-grams, so no stemmer or
  per-language tokenizer is needed.
* **No vocabulary file.** Hashing maps every feature straight to a column, so
  a word never seen in training still lands somewhere sensible, and the model
  artifact is just a weight matrix.
* **Stable across processes.** Python's built-in ``hash()`` is salted per
  process, so it would silently scramble a saved model on restart; CRC32 is
  used instead.

Normalisation folds the spelling variation that carries no meaning here:
Unicode compatibility forms, case, Arabic diacritics and letter variants (أ/إ/آ
to ا, ى to ي, ة to ه), Russian ё to е. Digits collapse to 0 and email
addresses and URLs to a placeholder, so an account number or address never
becomes a feature - the model learns what a complaint is *about*, not whose
it is.
"""

from __future__ import annotations

import math
import re
import unicodedata
import zlib
from collections import Counter

import numpy as np

DEFAULT_DIM = 1 << 15
DEFAULT_CHAR_NGRAMS = (2, 4)

_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
_EMAIL_RE = re.compile(r"[^\s@<>()]+@[^\s@<>()]+")
_DIGITS_RE = re.compile(r"\d+")
_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
# Harakat, Quranic marks and tatweel: presentation, not spelling.
_ARABIC_MARKS_RE = re.compile(r"[ؐ-ًؚ-ٰٟۖ-ۭـ]")
_ARABIC_FOLD = str.maketrans(
    {"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ى": "ي", "ة": "ه", "ؤ": "و", "ئ": "ي"}
)


def normalize(text: str) -> str:
    """Fold meaningless variation; see the module docstring."""
    text = unicodedata.normalize("NFKC", text or "").lower()
    text = _URL_RE.sub(" url ", text)
    text = _EMAIL_RE.sub(" email ", text)
    text = text.replace("ё", "е")
    text = _ARABIC_MARKS_RE.sub("", text).translate(_ARABIC_FOLD)
    return _DIGITS_RE.sub("0", text)


def tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(normalize(text))


def extract_features(
    text: str, char_ngrams: tuple[int, int] = DEFAULT_CHAR_NGRAMS
) -> Counter[str]:
    """Raw feature counts for one text, before hashing."""
    tokens = tokenize(text)
    feats: Counter[str] = Counter()
    for token in tokens:
        feats["w:" + token] += 1
    for left, right in zip(tokens, tokens[1:], strict=False):
        feats["b:" + left + " " + right] += 1
    lo, hi = char_ngrams
    for token in tokens:
        padded = f" {token} "
        for n in range(lo, hi + 1):
            for i in range(len(padded) - n + 1):
                feats["c:" + padded[i : i + n]] += 1
    return feats


class HashingVectorizer:
    """Maps text to an L2-normalised, sublinear-TF hashed feature vector.

    Signed hashing (the sign comes from a bit the column index does not use)
    makes colliding features cancel on average rather than always adding up.
    """

    def __init__(
        self, dim: int = DEFAULT_DIM, char_ngrams: tuple[int, int] = DEFAULT_CHAR_NGRAMS
    ) -> None:
        if dim <= 0 or dim & (dim - 1):
            raise ValueError("dim must be a positive power of two")
        self.dim = dim
        self.char_ngrams = (int(char_ngrams[0]), int(char_ngrams[1]))

    def config(self) -> dict:
        return {"kind": "hashing", "dim": self.dim, "char_ngrams": list(self.char_ngrams)}

    @classmethod
    def from_config(cls, config: dict) -> HashingVectorizer:
        return cls(dim=int(config["dim"]), char_ngrams=tuple(config["char_ngrams"]))

    def transform_one(self, text: str) -> tuple[np.ndarray, np.ndarray]:
        """Sparse form: (column indices, values), indices unique and sorted."""
        columns: dict[int, float] = {}
        mask = self.dim - 1
        for feature, count in extract_features(text, self.char_ngrams).items():
            h = zlib.crc32(feature.encode("utf-8"))
            sign = -1.0 if h & 0x80000000 else 1.0
            idx = h & mask
            columns[idx] = columns.get(idx, 0.0) + sign * (1.0 + math.log(count))
        if not columns:
            return np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.float32)
        indices = np.fromiter(sorted(columns), dtype=np.int64, count=len(columns))
        values = np.array([columns[i] for i in indices.tolist()], dtype=np.float32)
        norm = float(np.linalg.norm(values))
        if norm > 0:
            values /= norm
        return indices, values

    def transform(self, texts: list[str]) -> np.ndarray:
        """Dense matrix, one row per text. For training and batch evaluation."""
        matrix = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            indices, values = self.transform_one(text)
            matrix[row, indices] = values
        return matrix
