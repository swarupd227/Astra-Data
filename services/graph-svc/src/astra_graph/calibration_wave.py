"""The Calibration Report -- story S10.2.1, opening F10.2.

    "Calibration Report screen per F13.2"

**§15.3.1's own Calibration Report row content is F13.1/S13.1.2's report, not F13.2's.**
Confirmed by direct comparison: the spec's own row (§15.3.1) reads *"class-mix by tier,
coverage gauges, parity rates, C4 reasons histogram, family count, cost per report,
stage timings; comparison panel to the pre-calibration assumptions... Sign report"* --
which matches S13.1.2's own AC almost verbatim ("class mix per tier vs assumed
45/30/18/7, rule and pattern coverage, first-pass parity per tier, mean Mender passes,
C4 rate with reasons, family count and reports per family vs the 150 / 7 assumption...
cost per report by tier, elapsed time per stage, and the difference against the
pre-calibration assumptions... 'Sign report' by the Programme Manager and the client
analytics lead writes the calibrated baseline"). F13.2/S13.2.2, by contrast, is a
still-unbuilt *model-confidence* calibration-curve screen (`calibration.py`'s own module
docstring already discloses this as "unbuilt scope") -- a different concept entirely,
sharing only the English word "calibration." This module builds the report §15.3.1's own
row actually describes; the backlog AC's own cross-reference to F13.2 is read as the
identical kind of real, disclosed spec/backlog mismatch ADR 0071 already found once
before (S10.1.1's own §15.1/§14.4 divergence), not silently "corrected" without a trace.

**Not a Migration Unit re-baseline mechanism.** No "Calibration Wave" (`ReleaseTrain.
calibration: true`, F13.1's own S13.1.1) has ever been flagged anywhere in this
codebase -- this module reports on the *whole current estate*, the honest reading when
no train has ever actually been marked a calibration wave.

**Every field below is real, computed live from a store this codebase already writes to
for its own, earlier reason.** Two of S13.1.2's own named fields are honestly not built,
disclosed on the response rather than fabricated:

- **Elapsed time per stage** -- no reliable, uniformly-recorded stage-timestamp series
  exists across harvest/build/promotion yet (each stage's own store shapes its
  timestamps differently, and reconciling them into one "per stage" figure is real,
  separate work this story does not take on).
- **Executor strategy mix** -- no "execution strategy" concept exists anywhere in
  `case_execution.py` or elsewhere (confirmed by direct search).

**"Reports per family" has no spec-stated target, unlike the family count itself.** The
AC's own words name "the 150 / 7 assumption" for family count and reports-per-family
together, but only 150 (`retention.PLANNED_FAMILY_COUNT`) is ever stated anywhere in the
spec (confirmed by direct search for "7" as a per-family report assumption — no hit).
The real, observed mean is reported; no invented target is compared against it.

**"Sign report" writes `calibration_baseline`, a new, versioned, append-only table (see
migration v0037's own docstring for why versioned, not overwritten).** `signed_by` is
read from the calling principal (`ProgrammeManagerDep`); `countersigned_by` is a typed
name string, the identical "no separate authenticated second action" disclosed
limitation `g3_card.approve`/`g4_card.approve` already carry for their own countersign
field.
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass
from typing import Any

import asyncpg
from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .cartographer import list_families
from .classify import CALIBRATION_TARGETS, class_mix
from .errors import InvalidRequestError
from .graph.queries import NODE_INDEX_TABLE
from .ids import new_ulid
from .invoicing import UnitPriceStore
from .lineage import hydrate
from .patterns import list_patterns
from .redesign import APPENDIX_B_GUIDANCE
from .retention import PLANNED_FAMILY_COUNT
from .rules import rule_coverage
from .scope import TIERS, ScopeStore

BASELINE_TABLE = "public.calibration_baseline"

_UNSPECIFIED_C4_REASON = "unspecified -- classified before this platform recorded a rule id"


@dataclass(frozen=True, slots=True)
class CalibrationBaseline:
    id: str
    version: int
    report: dict[str, Any]
    signed_by: str
    countersigned_by: str
    signed_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "version": self.version, "report": self.report,
            "signed_by": self.signed_by, "countersigned_by": self.countersigned_by,
            "signed_at": self.signed_at,
        }


async def _first_pass_parity_by_tier(
    pool: asyncpg.Pool, graph_name: str, scope_store: ScopeStore,
) -> dict[str, dict[str, Any]]:
    """The identical §16.6 first-pass definition, grouped by each case's own workbook
    tier (`ScopeStore.states()`) rather than estate-wide -- S13.1.2's own literal
    "first-pass parity per tier"."""
    async with pool.acquire() as conn:
        run_rows = await conn.fetch(
            f"""SELECT id FROM {NODE_INDEX_TABLE}
             WHERE graph = $1 AND kind = 'node' AND label = 'ParityRun' AND retired_at IS NULL""",
            graph_name,
        )
        runs = await hydrate(conn, graph_name, "ParityRun", [row["id"] for row in run_rows])
        all_verdict_ids = sorted({vid for props in runs.values() for vid in (props.get("verdicts") or [])})
        verdicts = await hydrate(conn, graph_name, "Verdict", all_verdict_ids)

        case_rows = await conn.fetch(
            f"""SELECT id FROM {NODE_INDEX_TABLE}
             WHERE graph = $1 AND kind = 'node' AND label = 'ParityCase' AND retired_at IS NULL""",
            graph_name,
        )
        cases = await hydrate(conn, graph_name, "ParityCase", [row["id"] for row in case_rows])

    states = await scope_store.states()

    ordered_run_ids = sorted(runs, key=lambda rid: str(runs[rid].get("started") or ""))
    first_verdict_by_case: dict[str, dict[str, Any]] = {}
    for rid in ordered_run_ids:
        for vid in runs[rid].get("verdicts") or []:
            verdict = verdicts.get(vid)
            if verdict is None:
                continue
            case_ref = str(verdict.get("case_ref"))
            first_verdict_by_case.setdefault(case_ref, verdict)

    totals: dict[str, int] = dict.fromkeys(TIERS, 0)
    passes: dict[str, int] = dict.fromkeys(TIERS, 0)
    for case_id, verdict in first_verdict_by_case.items():
        case_props = cases.get(case_id)
        if case_props is None:
            continue
        workbook_id = case_props.get("mu_ref")
        state = states.get(str(workbook_id)) if workbook_id else None
        tier = state.tier if state else None
        if tier not in TIERS:
            continue
        totals[tier] += 1
        if verdict.get("result") == "PASS":
            passes[tier] += 1

    return {
        tier: {
            "cases": totals[tier],
            "first_pass": passes[tier],
            "first_pass_rate": (passes[tier] / totals[tier]) if totals[tier] else None,
        }
        for tier in TIERS
    }


async def _mean_mender_passes(pool: asyncpg.Pool, graph_name: str) -> dict[str, Any]:
    """Estate-wide -- `parity_dashboard.py`'s own `_closed_exception_case_pass_counts`,
    without the per-workbook filter."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""SELECT id FROM {NODE_INDEX_TABLE}
             WHERE graph = $1 AND kind = 'node' AND label = 'ExceptionCase' AND retired_at IS NULL""",
            graph_name,
        )
        cases = await hydrate(conn, graph_name, "ExceptionCase", [row["id"] for row in rows])
    counts = [
        int(props["passes_consumed"])
        for props in cases.values()
        if props.get("state") == "CLOSED" and props.get("passes_consumed") is not None
    ]
    if not counts:
        return {"available": False, "detail": "no ExceptionCase has closed through the Mender yet"}
    return {"available": True, "closed_count": len(counts), "mean_passes_to_pass": sum(counts) / len(counts)}


async def _c4_reasons(pool: asyncpg.Pool, graph_name: str) -> dict[str, Any]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""SELECT id FROM {NODE_INDEX_TABLE}
             WHERE graph = $1 AND kind = 'node' AND label = 'CalculatedField' AND retired_at IS NULL""",
            graph_name,
        )
        fields = await hydrate(conn, graph_name, "CalculatedField", [row["id"] for row in rows])

    total = len(fields)
    c4_fields = [props for props in fields.values() if props.get("class") == "C4"]
    histogram: dict[str, dict[str, Any]] = {}
    for props in c4_fields:
        rule_id = str(props.get("pattern_ref") or "")
        guidance = APPENDIX_B_GUIDANCE.get(rule_id)
        entry = histogram.setdefault(
            rule_id or "unspecified",
            {"count": 0, "guidance": guidance.appendix_b_guidance if guidance else _UNSPECIFIED_C4_REASON},
        )
        entry["count"] += 1

    return {
        "c4_count": len(c4_fields),
        "total_count": total,
        "c4_rate": (len(c4_fields) / total) if total else None,
        "by_reason": histogram,
    }


async def _family_and_report_counts(pool: asyncpg.Pool, graph_name: str) -> dict[str, Any]:
    families = await list_families(pool, graph_name)
    family_count = len(families)
    total_members = sum(len(family["members"]) for family in families)
    return {
        "family_count": family_count,
        "planned_family_count": PLANNED_FAMILY_COUNT,
        "reports_total": total_members,
        "mean_reports_per_family": (total_members / family_count) if family_count else None,
    }


async def _parse_quality(pool: asyncpg.Pool, graph_name: str) -> dict[str, Any]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""SELECT id FROM {NODE_INDEX_TABLE}
             WHERE graph = $1 AND kind = 'node' AND label = 'Workbook' AND retired_at IS NULL""",
            graph_name,
        )
        workbooks = await hydrate(conn, graph_name, "Workbook", [row["id"] for row in rows])
    scores = [
        float(props["parse_quality"])
        for props in workbooks.values()
        if props.get("parse_quality") is not None
    ]
    return {
        "workbooks_scored": len(scores),
        "workbooks_total": len(workbooks),
        "mean_parse_quality": (sum(scores) / len(scores)) if scores else None,
    }


async def _pattern_coverage(pool: asyncpg.Pool, graph_name: str) -> dict[str, Any]:
    patterns = await list_patterns(pool, graph_name)
    active = [p for p in patterns if p.get("promotion_state") == "ACTIVE"]
    return {"active_count": len(active), "total_count": len(patterns)}


async def _cost_per_report_by_tier(unit_price_store: UnitPriceStore) -> dict[str, float]:
    return await unit_price_store.all()


async def latest_baseline(pool: asyncpg.Pool, graph_name: str) -> CalibrationBaseline | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT * FROM {BASELINE_TABLE} WHERE graph = $1 ORDER BY version DESC LIMIT 1",
            graph_name,
        )
    if row is None:
        return None
    return _baseline_from_row(row)


def _baseline_from_row(row: asyncpg.Record) -> CalibrationBaseline:
    report = row["report"]
    return CalibrationBaseline(
        id=row["id"],
        version=int(row["version"]),
        report=report if isinstance(report, dict) else json.loads(report),
        signed_by=row["signed_by"],
        countersigned_by=row["countersigned_by"],
        signed_at=row["signed_at"].isoformat(),
    )


async def calibration_report(
    pool: asyncpg.Pool,
    graph_name: str,
    *,
    scope_store: ScopeStore,
    unit_price_store: UnitPriceStore,
) -> dict[str, Any]:
    """S13.1.2's own report, live -- see this module's own docstring for which two named
    fields are honestly not built, and why "reports per family" carries no target."""
    report = {
        "class_mix": await class_mix(pool, graph_name),
        "calibration_targets": CALIBRATION_TARGETS,
        "rule_coverage": await rule_coverage(pool, graph_name),
        "pattern_coverage": await _pattern_coverage(pool, graph_name),
        "first_pass_parity_by_tier": await _first_pass_parity_by_tier(pool, graph_name, scope_store),
        "mean_mender_passes": await _mean_mender_passes(pool, graph_name),
        "c4": await _c4_reasons(pool, graph_name),
        "families": await _family_and_report_counts(pool, graph_name),
        "parse_quality": await _parse_quality(pool, graph_name),
        "cost_per_report_by_tier": await _cost_per_report_by_tier(unit_price_store),
        "elapsed_time_per_stage": {
            "available": False,
            "detail": "no uniform stage-timestamp series exists across harvest/build/promotion yet",
        },
        "executor_strategy_mix": {
            "available": False,
            "detail": "no execution-strategy concept exists anywhere in this codebase",
        },
    }

    baseline = await latest_baseline(pool, graph_name)
    return {
        "report": report,
        "baseline": baseline.as_dict() if baseline else None,
        "comparison": _compare(report, baseline.report) if baseline else None,
    }


def _compare(current: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    """A shallow, honest diff of the headline scalars only -- the AC's own words are "the
    difference against the pre-calibration assumptions," not a structural diff of every
    nested histogram."""
    return {
        "class_mix_delta": {
            tier: current["class_mix"].get("counts", {}).get(tier, 0)
            - baseline.get("class_mix", {}).get("counts", {}).get(tier, 0)
            for tier in CALIBRATION_TARGETS
        },
        "rule_coverage_delta": (current["rule_coverage"].get("percentage") or 0)
        - (baseline.get("rule_coverage", {}).get("percentage") or 0),
        "family_count_delta": current["families"]["family_count"] - baseline.get("families", {}).get("family_count", 0),
        "c4_rate_delta": (current["c4"].get("c4_rate") or 0) - (baseline.get("c4", {}).get("c4_rate") or 0),
    }


async def sign_report(
    pool: asyncpg.Pool,
    graph_name: str,
    *,
    scope_store: ScopeStore,
    unit_price_store: UnitPriceStore,
    countersigned_by: str,
    signed_by: str,
) -> CalibrationBaseline:
    if not countersigned_by.strip():
        raise InvalidRequestError("signing a Calibration Report needs the client analytics lead's own name")

    live = await calibration_report(pool, graph_name, scope_store=scope_store, unit_price_store=unit_price_store)
    baseline_id = f"calbaseline_{new_ulid()}"

    async with pool.acquire() as conn, conn.transaction():
        version = (
            await conn.fetchval(
                f"SELECT MAX(version) FROM {BASELINE_TABLE} WHERE graph = $1", graph_name,
            )
            or 0
        ) + 1
        row = await conn.fetchrow(
            f"""INSERT INTO {BASELINE_TABLE}
                 (id, graph, version, report, signed_by, countersigned_by)
                 VALUES ($1, $2, $3, $4::jsonb, $5, $6)
                 RETURNING *""",
            baseline_id, graph_name, version, json.dumps(live["report"]), signed_by, countersigned_by.strip(),
        )
    assert row is not None
    return _baseline_from_row(row)


# ------------------------------------------------------------------------------ export


def render_calibration_report_pdf(result: dict[str, Any]) -> bytes:
    """§15.3.1's own "export" action -- a real PDF, tables not native charts (the
    identical, disclosed scope `status_pack.py`'s own export functions already
    carry)."""
    report = result["report"]
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=LETTER)
    styles = getSampleStyleSheet()
    story: list[Any] = [
        Paragraph("Astra Data — Calibration Report", styles["Title"]),
        Spacer(1, 12),
        Paragraph("Class mix vs calibration targets", styles["Heading2"]),
    ]
    mix = report["class_mix"]
    mix_rows = [["Class", "Count", "%", "Target"]]
    for class_, target in report["calibration_targets"].items():
        mix_rows.append(
            [class_, str(mix["counts"].get(class_, 0)), f"{mix['percentages'].get(class_, 0)}%", f"{target}%"]
        )
    story.append(_table(mix_rows))
    story.append(Spacer(1, 12))

    story.append(Paragraph("Coverage and parity", styles["Heading2"]))
    coverage_rows = [
        ["Figure", "Value"],
        ["Rule coverage", f"{report['rule_coverage']['percentage']}%"],
        ["Active patterns", str(report["pattern_coverage"]["active_count"])],
        ["Family count", f"{report['families']['family_count']} (planned {report['families']['planned_family_count']})"],
        ["Mean reports per family", str(report["families"]["mean_reports_per_family"])],
        ["Mean parse quality", str(report["parse_quality"]["mean_parse_quality"])],
        ["C4 rate", f"{(report['c4']['c4_rate'] or 0) * 100:.1f}%"],
    ]
    story.append(_table(coverage_rows))
    story.append(Spacer(1, 12))

    story.append(Paragraph("Cost per report by tier", styles["Heading2"]))
    price_rows = [["Tier", "Unit price"]] + [
        [tier, f"${price:,.0f}"] for tier, price in report["cost_per_report_by_tier"].items()
    ]
    story.append(_table(price_rows))

    if result.get("comparison"):
        story.append(Spacer(1, 12))
        story.append(Paragraph("Comparison to the last signed baseline", styles["Heading2"]))
        comparison_rows = [["Figure", "Delta"]] + [
            [key, str(value)] for key, value in result["comparison"].items()
        ]
        story.append(_table(comparison_rows))

    doc.build(story)
    return buffer.getvalue()


def _table(rows: list[list[str]]) -> Table:
    table = Table(rows)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f5fd0")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
            ]
        )
    )
    return table


__all__ = [
    "BASELINE_TABLE",
    "CalibrationBaseline",
    "calibration_report",
    "latest_baseline",
    "render_calibration_report_pdf",
    "sign_report",
]
