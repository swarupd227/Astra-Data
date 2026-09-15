"""Inference-boundary redaction -- spec §18.3, stories S11.4.1/S11.4.2, F11.4.

    S11.4.1: "Parity evidence headers and deltas (key values redacted to hashes;
    measure values redacted to sign-and-magnitude buckets)... Mender repair context is
    assembled from evidence bundles after redaction."

    S11.4.2: "Redaction pass removes literals that match data-like patterns (account
    numbers, emails, long numeric literals) from free-text fields, logging the
    redaction count."

Two real, independent redaction mechanisms, applied at two different points:

**1. Structural (S11.4.1), applied by the one real caller that touches row-level
data.** `Verdict.failing_cells` (§10.3's own "bounded sample" of real parity-diff cells
-- real dimension values plus real expected/candidate measure values,
`diff.FailingCell.as_dict()`) is read back from each case's own evidence artefact by
`mender._gather_parity_evidence` and placed into `RepairContext.failing_cells` -- one
field of the one real request shape that ever reaches `gateway.generate(task_class=
MENDER_REPAIR, ...)`. `mender.assemble_repair_context` redacts these cells (`redact_
failing_cell`, below) immediately after `_gather_parity_evidence` returns them, before
they ever reach `RepairContext` -- after §8.1.1's own `classification_signals` have
already been computed and stored on the `ExceptionCase` (confirmed: `assemble_repair_
context` reads `classification_signals` from `exception_properties`, never re-derives
it from `failing_cells`), so this changes nothing about the Mender's own deterministic
classification or pattern-matching, only what a reasoning-tier model call receives.
`generation.build_generation_request` (the Transpiler's own path) never touches a
row-level node at all (confirmed by direct reading: only calc formulas/ASTs, field/
parameter/worksheet *names*, and Pattern *templates*), so it needed no structural
redaction of its own.

**2. Pattern-based (S11.4.2), applied by the gateway itself to every real request,
regardless of caller.** A field-name-agnostic scan (`redact_data_like_literals`,
below) walking every string leaf value in a request's own `as_dict()` payload for text
that merely *looks like* data -- an email, an account-number-shaped digit group, a long
numeric literal -- and redacting the match in place. Applied inside `gateway.
ModelGateway.generate`/`StaticGateway.generate`, on the *already-redacted* payload
S11.4.1's own structural pass has already produced (mender's own cells arrive already
hashed/bucketed by the time the gateway sees them) -- this second pass is a real,
independent safety net over free text a caller never anticipated needing structural
redaction for (a calc formula literally containing `"john@example.com"`, say), not a
duplicate of the first. Unlike the structural pass, this one mutates the request that
actually reaches the provider, not a value the caller chose to redact of its own
accord -- the AC's own "so a prompt bug cannot leak data" is a claim about the real
outbound request, not merely about what gets logged afterward.

**Two rules, matching §18.3's own two named structural transformations, nothing
invented beyond them.** A key/grain value hashes to a short, non-reversible digest —
the same real value hashes identically every time, so a reviewing model can still tell
"these two failing cells share a key" without ever learning what that key actually is.
A measure value (`expected`/`candidate`) buckets to its own sign and order of magnitude
(`"+1e2"`, `"-1e0"`, `"0"`, `"null"`) — enough for a repair model to reason about the
*shape* of a discrepancy (a sign flip, an order-of-magnitude gap, an unexpected null)
without ever receiving the real number.

**The pattern scanner is a real, disclosed heuristic, not a guarantee.** A regex over
free text will occasionally redact a legitimate large constant (a real threshold, a
real row count) that merely looks like an account number or a long literal --
accepted, deliberately: a false positive costs a slightly less specific prompt; a false
negative is the exact leak this story exists to prevent. It will just as certainly miss
a real data value in a shape it does not recognise -- disclosed as a real, bounded
safety net alongside structural redaction and field-schema validation
(`gateway.validate_task_class_schema`), never the only layer.
"""

from __future__ import annotations

import hashlib
import math
import re
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
    "Every free-text field of every real gateway request is scanned for literals that "
    "look like an email address, an account-number-shaped digit group, or a long bare "
    "numeric literal before it reaches a model endpoint -- each match is replaced with "
    "a labelled placeholder, and the count of redactions is logged.",
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


# --------------------------------------------------------- S11.4.2: pattern-based scan

#: §18.3/S11.4.2's own three named shapes. Order matters: EMAIL and ACCOUNT_NUMBER are
#: applied before LONG_NUMERIC_LITERAL so a grouped, account-shaped digit run is
#: labelled as an account number rather than (also) tripping the plain long-digit rule
#: on what would already be redacted text.
EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
#: A credit-card/IBAN-shaped run: 3 or 4 groups of 4 digits, optionally space/dash
#: separated -- "account numbers" in the AC's own words, read as this common shape
#: rather than any single national account-number format (this platform has no one
#: client, and every real account/card number scheme shares this grouping).
ACCOUNT_NUMBER_PATTERN = re.compile(r"\b\d{4}[ \-]?\d{4}[ \-]?\d{4}[ \-]?\d{0,4}\b")
#: Anything else that is just a long run of digits -- a real account/phone/national-ID
#: number typed without grouping. `(?<!\d)`/`(?!\d)` keep this from matching the middle
#: of an even-longer digit run twice.
LONG_NUMERIC_LITERAL_PATTERN = re.compile(r"(?<!\d)\d{9,}(?!\d)")

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("EMAIL", EMAIL_PATTERN),
    ("ACCOUNT_NUMBER", ACCOUNT_NUMBER_PATTERN),
    ("LONG_NUMERIC_LITERAL", LONG_NUMERIC_LITERAL_PATTERN),
)


def _redact_string(value: str) -> tuple[str, int]:
    count = 0

    def _mark(rule_name: str) -> Any:
        def _replace(match: re.Match[str]) -> str:
            nonlocal count
            count += 1
            return f"[REDACTED:{rule_name}]"

        return _replace

    for rule_name, pattern in _PATTERNS:
        value = pattern.sub(_mark(rule_name), value)
    return value, count


def redact_data_like_literals(payload: Any) -> tuple[Any, int]:
    """Story S11.4.2: a field-name-agnostic scan over every string leaf value in a
    request payload (typically a `SupportsAsDict.as_dict()` result, but works over any
    JSON-shaped structure -- dicts, lists/tuples, and scalars alike), redacting
    anything that matches an email, an account-number-shaped digit group, or a long
    bare numeric literal. Returns `(redacted_payload, total_redaction_count)` -- the
    count is the AC's own "logging the redaction count," summed across every field the
    scan touched. Never mutates its input; always returns a fresh structure."""
    if isinstance(payload, str):
        return _redact_string(payload)
    if isinstance(payload, dict):
        total = 0
        redacted_dict: dict[Any, Any] = {}
        for key, value in payload.items():
            redacted_value, sub_count = redact_data_like_literals(value)
            redacted_dict[key] = redacted_value
            total += sub_count
        return redacted_dict, total
    if isinstance(payload, list | tuple):
        total = 0
        redacted_list: list[Any] = []
        for item in payload:
            redacted_item, sub_count = redact_data_like_literals(item)
            redacted_list.append(redacted_item)
            total += sub_count
        return (redacted_list if isinstance(payload, list) else tuple(redacted_list)), total
    return payload, 0


__all__ = [
    "ACCOUNT_NUMBER_PATTERN",
    "EMAIL_PATTERN",
    "LONG_NUMERIC_LITERAL_PATTERN",
    "REDACTION_RULES",
    "bucket_measure_value",
    "hash_key_value",
    "redact_data_like_literals",
    "redact_failing_cell",
]
