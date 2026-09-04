"""Canonical JSON, and the hash taken over it.

S1.3.1: "the assembler returns a canonical JSON document and its sha256 (context_hash)"
and "two calls with the same graph state and contract produce the same hash".

The hash is not a convenience. Specification §4.2 puts ``context_hash`` in every
provenance record and §5.4 has the gateway "caches identical context hashes", so the same
calculation assembled twice must hash the same or the cache never hits and no provenance
record can be checked against a re-assembly.

Canonical means every degree of freedom removed:

* **Keys sorted.** Python preserves insertion order, which depends on the order the
  assembler happened to build a dictionary in.
* **No insignificant whitespace.** ``separators=(",", ":")``.
* **Unicode kept as text, not escaped.** ``ensure_ascii=False``, then encoded UTF-8 once.
  A workbook named in Devanagari hashes the same as it reads.
* **No NaN or Infinity.** They are not JSON, and Python emits them anyway unless told not
  to. A float that cannot be serialised is a defect in the record, so it raises.
* **Collections ordered by the assembler**, not here — sorting nodes by id is the
  assembler's job because only it knows what the ordering means.

What the document must *not* contain is anything that changes without the graph changing:
no assembly timestamp, no request id, no service version. That is enforced by the
contracts choosing fields, not by this module, but it is the reason this module exists.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

#: The prefix §4.2 writes into provenance records: ``context_hash: sha256:…``.
HASH_PREFIX = "sha256"


class CanonicalisationError(ValueError):
    """A value cannot be represented canonically."""


def canonical_json(document: Any) -> bytes:
    """The document as canonical UTF-8 JSON."""
    try:
        text = json.dumps(
            document,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except ValueError as exc:  # NaN, Infinity, or a circular reference
        raise CanonicalisationError(
            f"the assembled context cannot be represented as canonical JSON: {exc}"
        ) from exc
    except TypeError as exc:
        raise CanonicalisationError(
            f"the assembled context contains a value JSON cannot hold: {exc}"
        ) from exc
    return text.encode("utf-8")


def context_hash(payload: bytes) -> str:
    """``sha256:<hex>`` over the canonical bytes."""
    return f"{HASH_PREFIX}:{hashlib.sha256(payload).hexdigest()}"


__all__ = ["HASH_PREFIX", "CanonicalisationError", "canonical_json", "context_hash"]
