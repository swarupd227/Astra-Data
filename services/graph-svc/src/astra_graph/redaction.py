"""Inference-boundary redaction -- spec §18.3, story S11.4.1, opens F11.4.

    "Parity evidence headers and deltas (key values redacted to hashes; measure values
    redacted to sign-and-magnitude buckets)... Mender repair context is assembled from
    evidence bundles after redaction."

**The one real, row-level-data-adjacent channel this codebase has into a model
endpoint**, confirmed by direct research: `Verdict.failing_cells` (§10.3's own "bounded
sample" of real parity-diff cells -- real dimension values plus real expected/candidate
measure values, `diff.FailingCell.as_dict()`) is read back from each case's own evidence
artefact by `mender._gather_parity_evidence` and placed into `RepairContext.failing_cells`
-- the one field of the one real request shape (`RepairContext.as_dict()`) that ever
reaches `gateway.generate(task_class=MENDER_REPAIR, ...)`. Nothing in the Transpiler's
own `generation.build_generation_request` ever touches a row-level node at all (confirmed
by direct reading: only calc formulas/ASTs, field/parameter/worksheet *names*, and
Pattern *templates* — no `ParityCase`/`Verdict`/`ResultSet`/evidence-artefact read
anywhere in that path), so there is no second redaction point this module needs to cover.

**Applied once, as late as safely possible.** `mender.assemble_repair_context` redacts
`failing_cells` immediately after `_gather_parity_evidence` returns them and before they
are placed into `RepairContext` — after §8.1.1's own `classification_signals` have
already been computed and stored on the `ExceptionCase` (confirmed: `assemble_repair_
context` reads `classification_signals` from `exception_properties`, never re-derives it
from `failing_cells`), so redaction here changes nothing about the Mender's own
deterministic classification or pattern-matching, only what a reasoning-tier model
call actually receives.

**Two rules, matching §18.3's own two named transformations, nothing invented beyond
them.** A key/grain value hashes to a short, non-reversible digest — the same real value
hashes identically every time, so a reviewing model can still tell "these two failing
cells share a key" without ever learning what that key actually is. A measure value
(`expected`/`candidate`) buckets to its own sign and order of magnitude (`"+1e2"`,
`"-1e0"`, `"0"`, `"null"`) — enough for a repair model to reason about the *shape* of a
discrepancy (a sign flip, an order-of-magnitude gap, an unexpected null) without ever
receiving the real number.
"""

from __future__ import annotations

import hashlib
import math
from typing import Any

#: Shown verbatim on the Data Handling screen (`data_handling.py`) as this tenant's own
#: recorded redaction rules -- the default a freshly saved `DataHandlingPosition` starts
#: from, and the literal English description of what `redact_failing_cell` below does.
REDACTION_RULES: tuple[str, ...] = (
    "Key/grain values (the real dimension values identifying a failing row) are "
    "redacted to a short, non-reversible hash before reaching a model endpoint -- the "
    "same value hashes identically every time, so a repair model can tell two failing "
    "cells share a key without ever learning what that key actually is.",
    "Measure values (expected and candidate) are redacted to a sign-and-magnitude "
    "bucket (e.g. '+1e2') before reaching a model endpoint -- enough to reason about "
    "the shape of a discrepancy (a sign flip, an order-of-magnitude gap, an "
    "unexpected null) without ever receiving the real number.",
)


def hash_key_value(value: Any) -> str:
    """A short, non-reversible digest of one real key/grain value. Deterministic (the
    same input always hashes the same), so two cells that really do share a key are
    still visibly linked after redaction -- just never as the real value itself."""
    digest = hashlib.sha256(str(value).encode("utf-8")).hexdigest()
    return f"sha256:{digest[:16]}"


def bucket_measure_value(value: Any) -> str:
    """Sign-and-magnitude only -- `"null"` for an absent/None cell (a real, common
    failing-cell shape: a value present on one side, missing on the other), `"0"` for
    an exact zero, otherwise `"{+|-}1e{order}"` where `order = floor(log10(abs(value)))`.
    Never raises on a non-numeric value -- redaction must never be the thing that
    crashes a repair pass; an unparseable value redacts to the honest `"non-numeric"`
    rather than leaking its own real content by falling through unredacted."""
    if value is None:
        return "null"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "non-numeric"
    if number == 0:
        return "0"
    sign = "+" if number > 0 else "-"
    order = math.floor(math.log10(abs(number)))
    return f"{sign}1e{order}"


def redact_failing_cell(cell: dict[str, Any]) -> dict[str, Any]:
    """One `FailingCell.as_dict()`-shaped dict (or the identical shape read back from a
    stored evidence bundle), with `grain_key` hashed element-wise and `expected`/
    `candidate` bucketed -- every other field (`measure`, `kind`, `case_ref`, ...) is
    already metadata (a column/measure *name*, not a value) and passes through
    unchanged, matching §18.3's own "calculation... field... names" as real,
    boundary-safe content on its own."""
    redacted = dict(cell)
    grain_key = redacted.get("grain_key")
    if isinstance(grain_key, list | tuple):
        redacted["grain_key"] = [hash_key_value(v) for v in grain_key]
    for field in ("expected", "candidate"):
        if field in redacted:
            redacted[field] = bucket_measure_value(redacted[field])
    return redacted


__all__ = [
    "REDACTION_RULES",
    "bucket_measure_value",
    "hash_key_value",
    "redact_failing_cell",
]
