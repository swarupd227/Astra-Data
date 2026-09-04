"""Deterministic platform ids for source objects.

S1.2.1 requires a re-harvest of an unchanged workbook to be a no-op, and spec §8.4 makes
the Harvester idempotent on content hash. Neither is possible if a re-harvest invents new
identifiers for the same sheets and fields: the second run would write a parallel copy of
the estate.

So a node's id is derived from what it is in the source — the adapter's fragment key,
scoped by site — rather than issued at random. The same workbook harvested twice produces
the same ids, on any deployment, without the Harvester keeping a lookup table.

The result must still satisfy the ontology's ULID rule (26 characters of Crockford base32,
first character 0-7). It is a *derived* identifier in ULID clothing rather than a
time-ordered one, and this is the one place in the platform where that is true:

* the leading 48 bits, which a real ULID uses for a timestamp, are taken from the hash and
  masked into the valid range, so these ids do not sort by creation time;
* the remaining 80 bits are hash, which is what makes collisions negligible — at ten
  million elements the probability is around 1e-11.

A random ULID is still correct for anything not derived from a source object, which is why
``ids.new_ulid`` remains the default everywhere else.
"""

from __future__ import annotations

import hashlib

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_TIME_BITS = 48
_RANDOM_BITS = 80

#: Namespace, so a source key can never collide with an id derived for another purpose.
_NAMESPACE = b"astra.estate.source-identity.v1"


def _encode(value: int, length: int) -> str:
    out = [""] * length
    for index in range(length - 1, -1, -1):
        out[index] = _ALPHABET[value & 0x1F]
        value >>= 5
    return "".join(out)


def source_key(site: str, fragment_key: str) -> str:
    """The stable name of a source object, as the platform sees it.

    Site-scoped: two Tableau sites can hold workbooks with the same LUID after a restore,
    and they are different objects.
    """
    return f"{site}\x1f{fragment_key}"


def derive_id(site: str, fragment_key: str) -> str:
    """A ULID-shaped identifier derived from a source object's identity."""
    digest = hashlib.blake2b(
        source_key(site, fragment_key).encode("utf-8"), key=_NAMESPACE, digest_size=16
    ).digest()
    value = int.from_bytes(digest, "big")

    # The first character of a ULID encodes the top 3 bits of the 48-bit timestamp and
    # must be 0-7, so the timestamp is masked to 47 bits.
    timestamp = (value >> _RANDOM_BITS) & ((1 << (_TIME_BITS - 1)) - 1)
    randomness = value & ((1 << _RANDOM_BITS) - 1)
    return _encode(timestamp, 10) + _encode(randomness, 16)
