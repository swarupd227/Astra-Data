"""C4 redesign — Appendix B / specification §3.2, story S5.4.1 (opens F5.4).

    "As a migration engineer, I want C4 constructs flagged with the closest Power BI
    approach and routed to a redesign decision, so that no one wastes a proof cycle on
    something that has no equivalent. For each C4 the Transpiler writes the reason, the
    Appendix B guidance, and an ASSISTED-mode redesign suggestion (marked as such). The MU
    is BLOCKED until a Migration Engineer records the redesign decision (implement as
    suggested / alternative / drop with report-owner agreement). Decisions are visible to
    the report owner and referenced at G3."

**What this module is.** `classify.py`'s own `reason` (§9.1, since S5.1.1) already names
*which* Appendix B.1 rule produced a C4 verdict; this module adds the two things that
`reason` alone never carried. `APPENDIX_B_GUIDANCE` turns Appendix B.1's own literal
target/notes cell for each C4-producing rule into real data, keyed by the classifier's own
`rule_id` — the identical "turn the spec's own table into data" move that made B.1's
function-family table `classify.py`'s own `_FAMILY_CLASS` in the first place (ADR 0035).
`build_redesign_suggestion` composes a real, deterministic, actionable next step from that
same guidance — `AgentMode.ASSISTED`, the identical "a real, reproducible,
non-model-generated draft" footing `modeller.py`'s own grain-statement drafting already
established (S3.1.1/S4.1.2): never a model call, so there is no inference boundary to
police and no `ContextContract` needed (`ContractName.TRANSPILER_C4_REDESIGN` is a name
only, the same deviation `MODELLER_FAMILY` already is).

**No Migration Unit exists to block, so `CalculatedField.redesign_decision` is the
disclosed proxy for §3.2's own BLOCKED state.** Confirmed by direct research against the
spec, not assumed from this codebase's own prior claims: §4.1.1's node table declares no
`MigrationUnit` row at all — it is a control-plane concept spanning several existing nodes
(§3.1: a workbook, its model family, its artefacts, its parity verdicts, its gates), not
itself a graph node, and no story before this one has ever created a real MU record
anywhere. `redesign_decision` absent on the one real, existing per-construct record this
platform actually has (`CalculatedField`) is the honest stand-in: a C4 field with no
decision yet is exactly what this platform would otherwise call BLOCKED.

**What this module is not.** It does not build a `GateDecision`-shaped, generically visible
decision record (`GateDecision` already exists, §4.1.1, but every write path to date is
G2-specific) — that fuller mechanism, and the report-owner visibility it would give *by
construction*, is S8.3.1's own later, explicit scope (the Exception Desk, milestone I4). It
does not build a real G3 gate — `GateDecision(gate="G3")` has never been written by any
story, and building the actual gate card is S9.1.1/S9.1.2's own later, explicit scope
(milestone I5), a full two increments after this one (F5.4 sits in I3). This story writes
what its own acceptance criteria asks for now: a decision recorded on the real construct,
readable by the report owner (the first story to ever drive `Role.CLIENT_REPORT_OWNER`,
declared since S1.1.1 and gated nowhere until now — the same trajectory `migration_architect`/
`parity_engineer`/`platform_engineer` each already took) and by any Artizent role, with
nothing yet claiming to *be* G3 itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .context.canonical import context_hash
from .context.contract import ContractName
from .ids import new_ulid
from .principal import Principal
from .provenance import AgentMode, ProvenanceStore, new_record

_AGENT = "transpiler"
_AGENT_VERSION = "0.1.0"

#: The three outcomes the AC itself names, verbatim.
REDESIGN_DECISIONS: tuple[str, ...] = ("IMPLEMENT_AS_SUGGESTED", "ALTERNATIVE", "DROP")


class RedesignDecisionError(Exception):
    """A recorded decision does not satisfy the acceptance criteria."""


@dataclass(frozen=True, slots=True)
class _Guidance:
    appendix_b_guidance: str
    suggestion: str


#: Appendix B.1's own literal target/notes cell for every `classify.py` rule_id that can
#: produce a C4 verdict, plus a real, deterministic next-step suggestion composed from it.
#: Keyed by `ClassificationResult.rule_id` exactly as `classify.py` emits it today — a rule
#: id absent from this table would be a real bug in this module, not a silently-tolerated
#: gap (`c4_properties` below raises rather than guessing at unrecognised keys).
APPENDIX_B_GUIDANCE: dict[str, _Guidance] = {
    "b1:no_ast": _Guidance(
        appendix_b_guidance="No Appendix B family applies: no AST was captured for this "
        "calculation at all, so its target equivalent (if any) cannot be assessed from "
        "the source expression.",
        suggestion="Confirm the source workbook still has this calculation's formula "
        "recorded — a re-harvest may be needed before any redesign can be assessed.",
    ),
    "b1:unrecognised_construct": _Guidance(
        appendix_b_guidance="No Appendix B family applies: the grammar could not parse "
        "this construct at all, so no mapping can be assessed until the source "
        "expression itself is understood.",
        suggestion="Review the original Tableau formula with a report author; the "
        "grammar could not parse it, so a manual translation or a scope decision "
        "(implement differently / drop) is needed before generation can proceed.",
    ),
    "b1:table_calc_complex_unresolved": _Guidance(
        appendix_b_guidance="Appendix B.1, 'Table calc — complex': OFFSET/INDEX/RANK "
        "where the grain is fixed; this construct's own addressing does not resolve from "
        "any encoding sheet, so the grain cannot be confirmed.",
        suggestion="Confirm the intended partition/addressing with a report author, then "
        "either fix it explicitly in DAX once known, or simplify the visual so a single, "
        "unambiguous grain applies.",
    ),
    "b1:unrecognised_function": _Guidance(
        appendix_b_guidance="No Appendix B.1 row names this function; it is outside the "
        "platform's own function registry entirely.",
        suggestion="Confirm what this function is meant to compute with a report author; "
        "if there is a common DAX/M equivalent, it can be added to the platform's own "
        "function registry rather than redesigned by hand every time it recurs.",
    ),
    "b1:regexp": _Guidance(
        appendix_b_guidance="Appendix B.1, 'String': REGEXP_* maps to 'M or C4' — a "
        "Power Query (M) pass-through is the target when the source dialect supports it; "
        "no M-generation path exists in this platform yet, so C4 applies conservatively.",
        suggestion="Implement the equivalent pattern match/extract as a Power Query (M) "
        "step if the source dialect supports it, or precompute the derived value "
        "upstream before it reaches the model.",
    ),
    "b1:unmapped_family": _Guidance(
        appendix_b_guidance="No Appendix B.1 default class is recorded for this "
        "function's own family at all.",
        suggestion="Confirm the intended behaviour with a report author, then choose a "
        "DAX/M pattern that reproduces it directly, since no default Appendix B.1 "
        "mapping exists for this family.",
    ),
    "b1:rawsql": _Guidance(
        appendix_b_guidance="Appendix B.1, 'RAWSQL': M pass-through where the source "
        "dialect is supported, otherwise C4 by default.",
        suggestion="Replace with native model relationships/measures where possible; if "
        "the raw SQL is dialect-specific and cannot be reproduced, keep a native query "
        "step in Power Query only if the source dialect is supported.",
    ),
    "b1:unknown": _Guidance(
        appendix_b_guidance="No specific Appendix B.1 family applies; this function fell "
        "through to the platform's own default-unknown bucket.",
        suggestion="Confirm what this function is meant to compute with a report author, "
        "since it fell through to the platform's own default-unknown bucket rather than "
        "a recognised Appendix B.1 family.",
    ),
}

#: The properties this module ever writes onto a `CalculatedField` — used to know exactly
#: what to omit when a field is no longer C4 (§3.2's own BLOCKED proxy becomes stale the
#: moment the field stops needing a redesign decision at all).
C4_PROPERTIES: tuple[str, ...] = (
    "appendix_b_guidance",
    "redesign_suggestion",
    "redesign_suggestion_provenance_ref",
    "redesign_decision",
    "redesign_decision_reason",
    "redesign_decision_by",
    "redesign_decision_at",
)


async def c4_properties(
    provenance_store: ProvenanceStore,
    *,
    calc_id: str,
    rule_id: str,
    existing: dict[str, Any],
    graph_version: int,
    principal: Principal,
) -> dict[str, Any]:
    """What to write onto a C4 `CalculatedField` — guidance and suggestion recomputed
    whenever `rule_id` is new or has changed since the last run (idempotent otherwise, so
    a repeated `reclassify_estate` does not spam a fresh `ProvenanceRecord` for an
    unchanged verdict); any already-recorded `redesign_decision` (a Migration Engineer's
    own real work) is always carried through untouched."""
    guidance = APPENDIX_B_GUIDANCE.get(rule_id)
    if guidance is None:
        raise RedesignDecisionError(
            f"rule_id {rule_id!r} produced a C4 verdict but has no entry in "
            f"redesign.APPENDIX_B_GUIDANCE -- classify.py and redesign.py have drifted"
        )

    properties: dict[str, Any] = {
        "appendix_b_guidance": guidance.appendix_b_guidance,
        "redesign_suggestion": guidance.suggestion,
    }

    unchanged = (
        existing.get("pattern_ref") == rule_id
        and existing.get("redesign_suggestion") == guidance.suggestion
        and existing.get("redesign_suggestion_provenance_ref")
    )
    if unchanged:
        properties["redesign_suggestion_provenance_ref"] = existing["redesign_suggestion_provenance_ref"]
    else:
        provenance_id = f"prov_{new_ulid()}"
        record = new_record(
            id=provenance_id,
            artefact_kind="CALCULATED_FIELD_REDESIGN_SUGGESTION",
            artefact_ref=calc_id,
            artefact_content_hash=context_hash(guidance.suggestion.encode("utf-8")),
            agent=_AGENT,
            agent_version=_AGENT_VERSION,
            mode=AgentMode.ASSISTED,
            contract=ContractName.TRANSPILER_C4_REDESIGN,
            subject_id=calc_id,
            context_hash=context_hash(rule_id.encode("utf-8")),
            graph_version=graph_version,
            created_by=principal.value,
        )
        await provenance_store.record(record)
        properties["redesign_suggestion_provenance_ref"] = provenance_id

    for key in ("redesign_decision", "redesign_decision_reason", "redesign_decision_by", "redesign_decision_at"):
        if key in existing:
            properties[key] = existing[key]

    return properties


def validate_decision(decision: str, *, reason: str) -> None:
    """§16.5-style discipline: refuse a malformed decision at the API boundary rather than
    writing one the console would have to defend against later."""
    if decision not in REDESIGN_DECISIONS:
        raise RedesignDecisionError(
            f"'{decision}' is not one of {REDESIGN_DECISIONS} — the AC's own three "
            f"outcomes (implement as suggested / alternative / drop)"
        )
    if not reason.strip():
        raise RedesignDecisionError(
            "a redesign decision needs a real rationale — for DROP specifically, this is "
            "where the report-owner agreement the AC requires is recorded, since this "
            "platform has no separate co-sign workflow (that is G2's own mechanism, not "
            "rebuilt here)"
        )


__all__ = [
    "APPENDIX_B_GUIDANCE",
    "C4_PROPERTIES",
    "REDESIGN_DECISIONS",
    "RedesignDecisionError",
    "c4_properties",
    "validate_decision",
]
