"""Versioned sparse WD14 cache payloads (independent of the inference runtime)."""

from __future__ import annotations

import hashlib
import math
import struct
from collections.abc import Mapping

WD14_CACHE_MIN_SCORE = 0.10
WD14_CACHE_TTL_DAYS = 30
WD14_CACHE_PRUNE_LIMIT = 256

# Little endian, no native padding: magic/version, pair count, then uint32/float32.
_HEADER = struct.Struct("<4sI")
_PAIR = struct.Struct("<If")
_MAGIC = b"WDS1"


def wd14_labels_hash(labels: tuple[str, ...]) -> str:
    """Hash exact ordered UTF-8 names, length-prefixed to avoid ambiguity."""
    if not labels or len(labels) > 2**32 or len(set(labels)) != len(labels):
        raise ValueError("WD14 labels must be nonempty, unique and indexable by uint32")
    digest = hashlib.sha256()
    for label in labels:
        if not isinstance(label, str) or not label:
            raise ValueError("WD14 label names must be nonempty strings")
        encoded = label.encode("utf-8")
        digest.update(struct.pack("<I", len(encoded)))
        digest.update(encoded)
    return digest.hexdigest()


def encode_wd14_scores(scores: Mapping[str, float], labels: tuple[str, ...]) -> bytes:
    """Filter before float32 rounding; never change the caller's full RAM vector."""
    indices = {name: index for index, name in enumerate(labels)}
    pairs = []
    for name, value in scores.items():
        if name not in indices:
            raise ValueError(f"WD14 score has no label index: {name}")
        score = float(value)
        if not math.isfinite(score) or not 0.0 <= score <= 1.0:
            raise ValueError("WD14 scores must be finite probabilities")
        if score >= WD14_CACHE_MIN_SCORE:
            pairs.append((indices[name], score))
    pairs.sort()
    return _HEADER.pack(_MAGIC, len(pairs)) + b"".join(_PAIR.pack(*pair) for pair in pairs)


def decode_wd14_scores(payload: bytes, labels: tuple[str, ...]) -> dict[str, float]:
    """Return only retained scores; malformed/unsupported blobs raise ValueError."""
    if len(payload) < _HEADER.size:
        raise ValueError("truncated WD14 cache header")
    magic, count = _HEADER.unpack_from(payload)
    if magic != _MAGIC or len(payload) != _HEADER.size + count * _PAIR.size:
        raise ValueError("unsupported or malformed WD14 cache payload")
    result = {}
    previous = -1
    for index, score in _PAIR.iter_unpack(payload[_HEADER.size:]):
        if not previous < index < len(labels):
            raise ValueError("invalid WD14 cache label index")
        if not math.isfinite(score) or not WD14_CACHE_MIN_SCORE <= score <= 1.0:
            raise ValueError("invalid WD14 cache score")
        result[labels[index]] = score
        previous = index
    return result
