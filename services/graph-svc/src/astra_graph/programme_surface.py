"""The Programme Board's KPI strip, train swimlanes and milestone rail -- story S10.2.1,
opening F10.2.

    "As a programme manager, I want the Programme Board, Wave Board, Calibration Report
    and Status Pack, so that the programme's state is one screen and the status pack
    writes itself.

    Acceptance criteria:
    - Programme Board per §15.3.1: KPI strip (MUs by state, first-pass parity,
      absorption vs calibrated baseline, gates due this week, spend vs budget), train
      swimlanes with planned vs projected, blocked reasons, exceptions ageing,
      milestones"

§15.3.1 itself, verbatim, the Programme Board row: *"Top: KPI strip — MUs by state,
first-pass parity, absorption vs calibrated baseline, gates due this week, spend vs
budget. Middle: train swimlanes with planned vs projected bars, MU counts by state per
train, blocked reasons. Bottom: milestone rail and gate calendar."*

**"Exceptions ageing" is not built here.** `ProgrammeBoard.tsx`'s own `ExceptionAgeingPane`
(story S8.3.2, `classification.exception_ageing`) already renders exactly this AC
clause, on this exact screen, since before this story existed — reusing it is the honest
move, not rebuilding a duplicate.

**Every figure below is real, computed live from a store this codebase already writes
to for its own, earlier reason — this module adds no new storage.** Where the AC names
a concept with no real engine feature behind it, the reading is disclosed here rather
than invented silently:

- **MUs by state** reads `IN_TRAIN.state`, the same real, honestly-static proxy the
  Wave Board's own kanban already groups by (`trains.py`'s own docstring: "a card's
  state is set once, at proposal time... this module never changes that state"). A
  count across every train, not a richer state machine §3.2 has never been built.
- **First-pass parity** is the identical §16.6 definition `parity_dashboard.py` already
  computes per workbook (a case's own first-ever verdict, PASS or not), aggregated here
  across every case in the live estate rather than one workbook at a time — a genuinely
  new bulk query, not a new concept.
- **Absorption vs calibrated baseline** reads `adoption.py`'s own real views-ratio
  metric (S9.2.2) — "absorption" and "adoption" are read as the same real, measured
  fact here, since nothing in this codebase has ever built a separate "absorption"
  instrumentation (confirmed by direct search: zero hits anywhere). "Calibrated
  baseline" reads as the configured `AdoptionConfig.threshold` — the real, current
  comparison point this platform actually has — until a real Calibration Wave (see
  `calibration_wave.py`) has signed one of its own; disclosed on the response itself
  (`baseline_source`) rather than silently presented as if a Calibration Wave had run.
- **Gates due this week** reads `g2_reminders.py`'s own real SLA math — the only gate
  in this codebase with any due-date concept at all (confirmed by direct search: no
  G1/G3/G4 equivalent exists). "This week" reads as "within `_DUE_SOON_WORKING_DAYS`
  working days of its own SLA breach" (a family not yet breached, but close); the
  response discloses this is G2-only.
- **Spend vs budget** reads `invoicing.py`'s own real accepted value as "spend"; a
  currency "budget" is undefined anywhere in this codebase (confirmed by direct
  search), so "budget" here reads as the real, already-disclosed *planned* commercial
  value — `PLANNED_BY_TIER[tier] * unit_price[tier]`, summed — the identical planning
  assumption `invoicing.programme_acceptance_summary` already uses for "planned" units,
  extended to currency since a unit price already exists for every tier. Not a
  fabricated number: every input is a real, already-disclosed planning constant or a
  real, current price.
- **Train swimlanes / MU counts by state per train / blocked reasons** extend
  `trains.list_trains`'s own already-fetched member states with a real per-train
  breakdown, plus a real join onto `ExceptionCase(state="BLOCKED")` -- the real proxy
  for "blocked" every prior BLOCKED-adjacent story has used, since no Migration Unit
  node exists to carry a BLOCKED state directly (`ontology/nodes.py`'s own note).
  **The "reason" is the case's own real `decision` value, not a free-text field** --
  confirmed by direct search, `foundry_routing.route_to_foundry` is the one write site
  for `state = "BLOCKED"` anywhere in this codebase, and it never persists its own
  human-readable `detail_note` onto the graph (that text is returned to its caller
  only). `_BLOCKED_REASONS` maps each real `decision` value to a fixed, disclosed
  sentence rather than inventing a free-text reason this platform has never actually
  recorded.
- **Milestone rail** assembles real, already-dated facts into one timeline — each
  train's own planned/actual start and end dates, and every real `GateDecision`'s own
  timestamp — rather than a new `Milestone` node (confirmed: none exists anywhere,
  and nothing in the spec's own data model names one either). A "gate calendar" in the
  same sense: the same real `GateDecision` timestamps, grouped by gate.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

import asyncpg

from .adoption import AdoptionConfigStore, AdoptionStore, decommission_tracker
from .g2 import QuestionStore
from .g2_reminders import DEFAULT_SLA_WORKING_DAYS, pending_g2_reviews
from .graph.queries import NODE_INDEX_TABLE
from .invoicing import PLANNED_BY_TIER, UnitPriceStore, programme_acceptance_summary
from .lineage import hydrate
from .scope import TIERS
from .trains import list_trains

#: "Due this week" reads as "within five working days of its own SLA breach" -- the
#: identical figure `g2_reminders.DEFAULT_SLA_WORKING_DAYS` already uses for the breach
#: line itself, applied here as an early-warning band rather than a second constant.
_DUE_SOON_WORKING_DAYS = 5

#: Every real value `ExceptionCase.decision` takes for a BLOCKED case -- confirmed by
#: direct search, `foundry_routing.route_to_foundry` is the one write site for `state =
#: "BLOCKED"` anywhere in this codebase, and it always writes `decision =
#: "MODEL_DEFECT_FOUNDRY"`. No free-text reason is ever persisted on the `ExceptionCase`
#: itself (`route_to_foundry`'s own human-readable `detail_note` is returned to its
#: caller, not written to the graph) -- these fixed, disclosed sentences are the honest
#: reading of "the reason" a BLOCKED case's own real `decision` value can support today.
_BLOCKED_REASONS: dict[str, str] = {
    "MODEL_DEFECT_FOUNDRY": "routed to the Foundry as a model-defect change request",
}
_UNSPECIFIED_BLOCKED_REASON = "blocked, with no recorded decision yet"


# ------------------------------------------------------------------------- KPI strip


async def _mus_by_state(pool: asyncpg.Pool, graph_name: str) -> dict[str, int]:
    trains = await list_trains(pool, graph_name)
    counts: Counter[str] = Counter()
    for train in trains:
        for member in train["members"]:
            counts[str(member["state"])] += 1
    return dict(counts)


async def _estate_first_pass_rate(pool: asyncpg.Pool, graph_name: str) -> dict[str, Any]:
    """The identical §16.6 definition `parity_dashboard.aggregate_dashboard` already
    uses per workbook -- a case's own first-ever verdict across every run it was
    diffed in -- applied here across every case in the live estate in one pass."""
    async with pool.acquire() as conn:
        run_rows = await conn.fetch(
            f"""SELECT id FROM {NODE_INDEX_TABLE}
             WHERE graph = $1 AND kind = 'node' AND label = 'ParityRun' AND retired_at IS NULL""",
            graph_name,
        )
        runs = await hydrate(conn, graph_name, "ParityRun", [row["id"] for row in run_rows])
        all_verdict_ids = sorted({vid for props in runs.values() for vid in (props.get("verdicts") or [])})
        verdicts = await hydrate(conn, graph_name, "Verdict", all_verdict_ids)

    ordered_run_ids = sorted(runs, key=lambda rid: str(runs[rid].get("started") or ""))
    first_verdict_by_case: dict[str, dict[str, Any]] = {}
    for rid in ordered_run_ids:
        for vid in runs[rid].get("verdicts") or []:
            verdict = verdicts.get(vid)
            if verdict is None:
                continue
            case_ref = str(verdict.get("case_ref"))
            first_verdict_by_case.setdefault(case_ref, verdict)

    total = len(first_verdict_by_case)
    passed = sum(1 for v in first_verdict_by_case.values() if v.get("result") == "PASS")
    return {
        "cases": total,
        "first_pass": passed,
        "first_pass_rate": (passed / total) if total else None,
    }


async def _absorption(
    pool: asyncpg.Pool, graph_name: str, adoption_store: AdoptionStore, adoption_config_store: AdoptionConfigStore,
) -> dict[str, Any]:
    tracker = await decommission_tracker(pool, graph_name, adoption_store, adoption_config_store)
    ratios = [
        mu["snapshot"]["ratio"]
        for site in tracker["sites"]
        for mu in site["mus"]
        if mu["snapshot"] and mu["snapshot"]["ratio"] is not None
    ]
    meeting = sum(
        1
        for site in tracker["sites"]
        for mu in site["mus"]
        if mu["snapshot"] and mu["snapshot"]["meets_threshold"]
    )
    captured = sum(1 for site in tracker["sites"] for mu in site["mus"] if mu["snapshot"])
    return {
        "threshold": tracker["threshold"],
        "baseline_source": "the configured adoption threshold -- no Calibration Wave has signed a "
        "different one yet",
        "mean_ratio": (sum(ratios) / len(ratios)) if ratios else None,
        "captured_count": captured,
        "meeting_threshold_count": meeting,
    }


async def _gates_due_this_week(pool: asyncpg.Pool, graph_name: str, question_store: QuestionStore) -> dict[str, Any]:
    reviews = await pending_g2_reviews(pool, graph_name, question_store)
    due_soon = [
        review
        for review in reviews
        if not review.breached
        and review.days_waiting is not None
        and (DEFAULT_SLA_WORKING_DAYS - review.days_waiting) <= _DUE_SOON_WORKING_DAYS
    ]
    breached = [review for review in reviews if review.breached]
    return {
        "scope": "G2 only -- no G1/G3/G4 due-date concept exists yet",
        "due_this_week_count": len(due_soon),
        "already_breached_count": len(breached),
        "due_this_week": [
            {"family_id": r.family_id, "name": r.name, "days_waiting": r.days_waiting} for r in due_soon
        ],
    }


async def _spend_vs_budget(
    pool: asyncpg.Pool, graph_name: str, unit_price_store: UnitPriceStore,
) -> dict[str, Any]:
    summary = await programme_acceptance_summary(pool, graph_name, unit_price_store)
    prices = await unit_price_store.all()
    planned_value = sum(PLANNED_BY_TIER[tier] * prices[tier] for tier in TIERS)
    return {
        "spend": summary["total_accepted_value"],
        "budget": planned_value,
        "budget_source": "the planned unit count per tier (invoicing.PLANNED_BY_TIER) times each "
        "tier's own current unit price -- no currency figure named 'budget' exists anywhere in "
        "this codebase's own spec/backlog text",
        "delta": summary["total_accepted_value"] - planned_value,
    }


async def kpi_strip(
    pool: asyncpg.Pool,
    graph_name: str,
    *,
    question_store: QuestionStore,
    unit_price_store: UnitPriceStore,
    adoption_store: AdoptionStore,
    adoption_config_store: AdoptionConfigStore,
) -> dict[str, Any]:
    return {
        "mus_by_state": await _mus_by_state(pool, graph_name),
        "first_pass_parity": await _estate_first_pass_rate(pool, graph_name),
        "absorption": await _absorption(pool, graph_name, adoption_store, adoption_config_store),
        "gates_due_this_week": await _gates_due_this_week(pool, graph_name, question_store),
        "spend_vs_budget": await _spend_vs_budget(pool, graph_name, unit_price_store),
    }


# ------------------------------------------------------------------- train swimlanes


async def _blocked_reasons(pool: asyncpg.Pool, graph_name: str) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""SELECT id FROM {NODE_INDEX_TABLE}
             WHERE graph = $1 AND kind = 'node' AND label = 'ExceptionCase' AND retired_at IS NULL""",
            graph_name,
        )
        cases = await hydrate(conn, graph_name, "ExceptionCase", [row["id"] for row in rows])
    return [
        {
            "case_id": case_id,
            "workbook_id": props.get("mu_ref"),
            "class": props.get("class"),
            "decision": props.get("decision"),
            "reason": _BLOCKED_REASONS.get(str(props.get("decision")), _UNSPECIFIED_BLOCKED_REASON),
        }
        for case_id, props in cases.items()
        if props.get("state") == "BLOCKED"
    ]


async def train_swimlanes(pool: asyncpg.Pool, graph_name: str) -> dict[str, Any]:
    trains = await list_trains(pool, graph_name)
    blocked = await _blocked_reasons(pool, graph_name)
    blocked_by_workbook: dict[str, list[dict[str, Any]]] = {}
    for entry in blocked:
        workbook_id = entry.get("workbook_id")
        if workbook_id:
            blocked_by_workbook.setdefault(str(workbook_id), []).append(entry)

    lanes = []
    for train in trains:
        state_counts: Counter[str] = Counter()
        train_blocked: list[dict[str, Any]] = []
        for member in train["members"]:
            state_counts[str(member["state"])] += 1
            train_blocked.extend(blocked_by_workbook.get(member["id"], []))
        lanes.append(
            {
                "id": train["id"],
                "name": train["name"],
                "size": train["size"],
                "planned_start": train["planned_start"],
                "planned_end": train["planned_end"],
                "actual_start": train["actual_start"],
                "actual_end": train["actual_end"],
                "state_counts": dict(state_counts),
                "blocked": train_blocked,
                "blocked_count": len(train_blocked),
            }
        )
    return {
        "trains": lanes,
        # A BLOCKED case whose own workbook is not a live member of any train --
        # already retired from its train, or never one to begin with.
        "orphaned_blocked_count": len(blocked) - sum(lane["blocked_count"] for lane in lanes),
    }


# --------------------------------------------------------------------- milestone rail


async def milestone_rail(pool: asyncpg.Pool, graph_name: str) -> dict[str, Any]:
    trains = await list_trains(pool, graph_name)
    milestones: list[dict[str, Any]] = []
    for train in trains:
        for date_field, label in (
            ("planned_start", "planned start"),
            ("planned_end", "planned end"),
            ("actual_start", "actual start"),
            ("actual_end", "actual end"),
        ):
            when = train.get(date_field)
            if when:
                milestones.append(
                    {"date": when, "kind": "train", "label": f"{train['name']} — {label}", "ref": train["id"]}
                )

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""SELECT id FROM {NODE_INDEX_TABLE}
             WHERE graph = $1 AND kind = 'node' AND label = 'GateDecision' AND retired_at IS NULL""",
            graph_name,
        )
        decisions = await hydrate(conn, graph_name, "GateDecision", [row["id"] for row in rows])

    gate_calendar: dict[str, list[dict[str, Any]]] = {}
    for decision_id, props in decisions.items():
        gate = str(props.get("gate") or "")
        when = props.get("timestamp")
        if not when:
            continue
        entry = {
            "date": when,
            "kind": "gate",
            "label": f"{gate} {props.get('decision')} — {props.get('subject_ref')}",
            "ref": decision_id,
        }
        milestones.append(entry)
        gate_calendar.setdefault(gate, []).append(entry)

    milestones.sort(key=lambda m: str(m["date"]))
    for entries in gate_calendar.values():
        entries.sort(key=lambda m: str(m["date"]))

    return {"rail": milestones, "gate_calendar": gate_calendar}


__all__ = [
    "kpi_strip",
    "milestone_rail",
    "train_swimlanes",
]
