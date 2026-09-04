"""ULID generation.

The platform identifies graph nodes and edges with ULIDs (spec §3.1). They sort by
creation time, which matters for a graph written by many concurrent adapter workers: an
index range scan over ids is a time range scan.

Implemented here rather than pulled in as a dependency: it is twenty lines, and the
encoding has to match the validator in ``ontology/properties.py`` exactly.
"""

from __future__ import annotations

import os
import time

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford base32
_ENCODED_TIME_LENGTH = 10
_ENCODED_RANDOM_LENGTH = 16


def _encode(value: int, length: int) -> str:
    out = [""] * length
    for index in range(length - 1, -1, -1):
        out[index] = _ALPHABET[value & 0x1F]
        value >>= 5
    return "".join(out)


def new_ulid(*, timestamp_ms: int | None = None) -> str:
    """A new ULID: 48 bits of millisecond timestamp, 80 bits of randomness."""
    ms = timestamp_ms if timestamp_ms is not None else int(time.time() * 1000)
    if not 0 <= ms < (1 << 48):
        raise ValueError(f"timestamp out of ULID range: {ms}")
    randomness = int.from_bytes(os.urandom(10), "big")
    return _encode(ms, _ENCODED_TIME_LENGTH) + _encode(randomness, _ENCODED_RANDOM_LENGTH)


def timestamp_ms_of(ulid: str) -> int:
    """Extract the millisecond timestamp from a ULID."""
    if len(ulid) != _ENCODED_TIME_LENGTH + _ENCODED_RANDOM_LENGTH:
        raise ValueError(f"not a ULID: {ulid!r}")
    value = 0
    for char in ulid[:_ENCODED_TIME_LENGTH]:
        index = _ALPHABET.find(char)
        if index < 0:
            raise ValueError(f"not a ULID: {ulid!r}")
        value = (value << 5) | index
    return value
