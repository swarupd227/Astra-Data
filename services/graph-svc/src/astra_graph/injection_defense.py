"""Prompt-injection defence -- story S11.4.3, closes F11.4.

    "As a platform engineer, I want prompt-injection defence on source content, so
    that a hostile string in a workbook cannot steer an agent.

    Acceptance criteria:
    - Source content enters prompts only inside typed fields, never as instructions;
      the assembler escapes and delimits it
    - Gateway runs an injection classifier on typed content; hits are logged and the
      field is replaced with a placeholder plus an ExceptionCase for a human
    - Model output is validated against the schema before any use; an output
      containing instructions or references outside the schema is rejected
    - Red-team set of 200 injection cases runs in CI against the Transpiler and
      Mender paths; zero successful steering is the bar"

Spec §16.5, verbatim: *"Source workbook content ... is untrusted. It reaches a model
only inside typed fields of the context contract, never in the instruction position;
the gateway screens it with an injection classifier; and model outputs are validated
against schema before any use."*

**One module, two independent scans -- the same string content, checked at both ends
of a real gateway call.** `scan_payload_for_injection` runs on the *request*, before
it is ever sent (`gateway._dispatch`, on the fields `gateway.INJECTION_SCAN_FIELDS`
names as "typed content" for a given task class); `reject_if_injection` runs on the
*response*, inside `generation.ModelResponseSchema`/`mender.RepairResponseSchema`'s
own pydantic validators, at the identical rung-1 schema-check point `extra="forbid"`
already occupies. Neither call site is duplicated here -- this module owns detection
only, never dispatch or schema wiring.

**A real, disclosed heuristic, not a guarantee -- confirmed, by direct research, that
no ML training or serving infrastructure exists anywhere in this codebase.** The
identical honest-heuristic footing `redaction.redact_data_like_literals` (S11.4.2)
already established for its own pattern scanner: a real, working, checked-in set of
known injection phrasings (imperative override attempts, role-override attempts,
delimiter-breakout attempts, requests to reveal the system prompt), not a trained
classifier this platform has no infrastructure to build or serve. A false positive
costs a withheld field and a human review; a false negative is the exact steering
this story exists to prevent -- the same trade-off `redaction.py`'s own docstring
already discloses for its own scanner.

**"The field is replaced with a placeholder" is a whole-field replacement, not an
in-place substring edit.** `redact_data_like_literals` edits individual matched
substrings within a string (a leaked email becomes `[REDACTED:EMAIL]` inline, the
surrounding sentence survives); this module's own `scan_payload_for_injection`
replaces an entire flagged *field's* value with one fixed placeholder, wholesale --
the AC's own literal wording, and the safer choice for a field whose own hostile
content might not be cleanly separable from whatever legitimate text surrounds it.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

#: Known prompt-injection phrasings -- imperative override attempts, role-override
#: attempts, delimiter-breakout attempts (matching `gateway._build_prompt`'s own new
#: `<field>` delimiters, story S11.4.3's own first bullet), and requests to reveal the
#: system prompt. Case-insensitive; matched against every string leaf of a scanned
#: field's own value (which may itself be a nested dict/list). Order is declaration
#: order -- `scan_text_for_injection` reports every match, not just the first.
_INJECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "IGNORE_INSTRUCTIONS",
        re.compile(r"ignore\s+(all\s+|any\s+|the\s+)?(previous|prior|above)\s+instructions", re.IGNORECASE),
    ),
    ("DISREGARD_ABOVE", re.compile(r"disregard\s+(the\s+)?(above|previous|prior)", re.IGNORECASE)),
    ("NEW_INSTRUCTIONS", re.compile(r"new\s+instructions\s*:", re.IGNORECASE)),
    (
        "ROLE_OVERRIDE",
        re.compile(
            r"\byou\s+are\s+now\b|\bact\s+as\b|\bpretend\s+(you\s+are|to\s+be)\b|\broleplay\s+as\b",
            re.IGNORECASE,
        ),
    ),
    (
        "REVEAL_SYSTEM_PROMPT",
        re.compile(r"(reveal|show|print|repeat)\s+(your|the)\s+(system\s+)?(prompt|instructions)", re.IGNORECASE),
    ),
    (
        "DELIMITER_BREAKOUT",
        re.compile(r"</field|<field\b|<system\b|</system|\[INST\]|\[/INST\]|<<SYS>>|<</SYS>>", re.IGNORECASE),
    ),
    (
        "OUTPUT_OVERRIDE",
        re.compile(r"output\s+the\s+following\s+instead|respond\s+with\s+only\s+the\s+following", re.IGNORECASE),
    ),
)

#: What a flagged field's value becomes in the outbound payload -- the AC's own
#: literal "the field is replaced with a placeholder." Fixed and generic (never
#: echoes which pattern matched, or any of the withheld content) so the placeholder
#: itself cannot become a second injection surface.
PLACEHOLDER = "[REDACTED: this field was withheld by the prompt-injection scan -- see the platform's exception queue]"

#: A real `ExceptionCase.class` value -- the identical named-constant convention
#: `visual_redesign.REDESIGN_CLASS`/`regression.REGRESSION_CLASS` already established
#: for their own disclosed, non-§11.1 uses of this one taxonomy (`ontology/nodes.py`'s
#: own `SpecDeviation` entry for `ExceptionCase.class (INJECTION_SUSPECTED)` has the
#: full reasoning).
INJECTION_SUSPECTED_CLASS = "INJECTION_SUSPECTED"


def scan_text_for_injection(text: str) -> tuple[str, ...]:
    """Every distinct pattern name that matched `text`, in declared order -- an empty
    tuple when nothing matched."""
    return tuple(name for name, pattern in _INJECTION_PATTERNS if pattern.search(text))


def _scan_value(value: Any) -> tuple[str, ...]:
    """Recursively scans every string leaf of `value` (a dict, a list/tuple, or a
    scalar), returning the de-duplicated union of every pattern matched anywhere
    inside it. Never mutates `value`."""
    if isinstance(value, str):
        return scan_text_for_injection(value)
    if isinstance(value, Mapping):
        hits: list[str] = []
        for sub in value.values():
            hits.extend(_scan_value(sub))
        return tuple(dict.fromkeys(hits))
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        hits = []
        for sub in value:
            hits.extend(_scan_value(sub))
        return tuple(dict.fromkeys(hits))
    return ()


def scan_payload_for_injection(
    payload: Mapping[str, Any], fields: frozenset[str]
) -> tuple[dict[str, Any], dict[str, tuple[str, ...]]]:
    """Scans only `fields` -- the source-derived subset of a task class's own allowed
    fields (`gateway.INJECTION_SCAN_FIELDS`), never the platform-controlled ones
    (`constraints`, `output_schema`, `task`, ...) that never carry workbook content.
    Never mutates `payload`; a field absent from it is simply skipped (the same
    "nothing to check" posture `gateway.validate_task_class_schema` already has for a
    missing optional field). Returns `(scanned_payload, hits)`: `scanned_payload` is a
    shallow copy of `payload` with every flagged field's value replaced by
    `PLACEHOLDER`; `hits` maps each flagged field's own name to every pattern matched
    inside it (empty when nothing was flagged)."""
    hits: dict[str, tuple[str, ...]] = {}
    scanned: dict[str, Any] = dict(payload)
    for field in fields:
        if field not in payload:
            continue
        matched = _scan_value(payload[field])
        if matched:
            hits[field] = matched
            scanned[field] = PLACEHOLDER
    return scanned, hits


def reject_if_injection(value: str) -> str:
    """A pydantic-compatible field validator: raises `ValueError` naming every matched
    pattern when `value` itself looks like an injection attempt -- the AC's own "an
    output containing instructions... is rejected," applied to a real model response's
    own string fields (`generation.ModelResponseSchema`/`mender.RepairResponseSchema`)
    at the identical rung-1 schema-check point `extra="forbid"` already occupies, so a
    hit fails the same way any other schema violation already does (a real
    `ValidationError`, no retry, the prompt/response contract's own fault -- §16.1's
    already-established reasoning, not a new failure category)."""
    matched = scan_text_for_injection(value)
    if matched:
        raise ValueError(f"output rejected -- looks like a prompt-injection attempt: {', '.join(matched)}")
    return value


__all__ = [
    "INJECTION_SUSPECTED_CLASS",
    "PLACEHOLDER",
    "reject_if_injection",
    "scan_payload_for_injection",
    "scan_text_for_injection",
]
