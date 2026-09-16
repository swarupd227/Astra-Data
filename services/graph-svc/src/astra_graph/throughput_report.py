"""The throughput and cost report -- story S6.2.3.

    "As a project manager, I want custodians live per week, agent acceptance and
    credits per custodian per day, so that reporting is generated.

    Acceptance criteria:
    - Weekly report exported for the client cadence
    - Cost per custodian visible from query tags"

**"Weekly report exported for the client cadence" reuses the Status Pack's own real
shape (`status_pack.py`, story S10.2.1), confirmed by the user before any code was
written: "generated weekly" is a `POST`-triggered snapshot a Programme Manager asks
for, not a background scheduler this platform does not have (the same disclosed
`HarvestScheduler`-shaped gap `status_pack.py`'s own docstring already names). Every
generate call is a new, append-only row -- the identical "an edit/regenerate is a new
version" shape `status_pack`/`calibration_baseline` already established -- so a past
week's own real numbers stay inspectable even once the `gateway_request_log`/
`commercial_ledger` rows they were computed from have aged out of whatever retention
window a later story gives them.

**"Exported" is a real CSV, not a second JSON read.** Three clearly labelled sections
(one per metric) in one file -- the AC's own "weekly report" is tabular figures, not a
narrative with charts (`status_pack`'s own PDF/PPTX are the right shape for a narrative;
this report has none), so a single flat CSV -- the same `csv.writer` mechanism
`decision_register.decisions_to_csv` already uses -- is the honest, simplest shape for
"the client cadence," not a second export format invented for its own sake.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from typing import Any

import asyncpg

from .ids import new_ulid
from .throughput_metrics import (
    agent_acceptance_per_custodian_per_day,
    credits_per_custodian_per_day,
    custodians_live_per_week,
)

REPORT_TABLE = "public.throughput_report"

DEFAULT_WEEKS = 12
DEFAULT_DAYS = 30


@dataclass(frozen=True, slots=True)
class ThroughputReport:
    id: str
    weeks: int
    days: int
    custodians_live_per_week: list[dict[str, Any]]
    agent_acceptance_per_custodian_per_day: list[dict[str, Any]]
    credits_per_custodian_per_day: list[dict[str, Any]]
    generated_by: str
    generated_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "weeks": self.weeks,
            "days": self.days,
            "custodians_live_per_week": self.custodians_live_per_week,
            "agent_acceptance_per_custodian_per_day": self.agent_acceptance_per_custodian_per_day,
            "credits_per_custodian_per_day": self.credits_per_custodian_per_day,
            "generated_by": self.generated_by,
            "generated_at": self.generated_at,
        }


def _from_row(row: asyncpg.Record) -> ThroughputReport:
    report = row["report"]
    payload = report if isinstance(report, dict) else json.loads(report)
    return ThroughputReport(
        id=row["id"],
        weeks=int(row["weeks"]),
        days=int(row["days"]),
        custodians_live_per_week=payload["custodians_live_per_week"],
        agent_acceptance_per_custodian_per_day=payload["agent_acceptance_per_custodian_per_day"],
        credits_per_custodian_per_day=payload["credits_per_custodian_per_day"],
        generated_by=row["generated_by"],
        generated_at=row["generated_at"].isoformat(),
    )


async def latest_report(pool: asyncpg.Pool, graph_name: str) -> ThroughputReport | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT * FROM {REPORT_TABLE} WHERE graph = $1 ORDER BY generated_at DESC LIMIT 1",
            graph_name,
        )
    return _from_row(row) if row else None


async def generate_report(
    pool: asyncpg.Pool, graph_name: str, *,
    generated_by: str, weeks: int = DEFAULT_WEEKS, days: int = DEFAULT_DAYS,
) -> ThroughputReport:
    live = await custodians_live_per_week(pool, graph_name, weeks=weeks)
    acceptance = await agent_acceptance_per_custodian_per_day(pool, graph_name, days=days)
    credits = await credits_per_custodian_per_day(pool, graph_name, days=days)
    payload = {
        "custodians_live_per_week": live,
        "agent_acceptance_per_custodian_per_day": acceptance,
        "credits_per_custodian_per_day": credits,
    }
    report_id = f"throughput_{new_ulid()}"
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""INSERT INTO {REPORT_TABLE}
                 (id, graph, generated_by, weeks, days, report)
                 VALUES ($1, $2, $3, $4, $5, $6::jsonb)
                 RETURNING *""",
            report_id, graph_name, generated_by, weeks, days, json.dumps(payload),
        )
    assert row is not None
    return _from_row(row)


# ------------------------------------------------------------------------------ export


def render_csv(report: ThroughputReport) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([f"Astra Data -- Throughput and cost report, generated {report.generated_at}"])
    writer.writerow([f"by {report.generated_by} -- trailing {report.weeks} weeks / {report.days} days"])
    writer.writerow([])

    writer.writerow(["Custodians live per week"])
    writer.writerow(["week_of", "custodians_live"])
    for row in report.custodians_live_per_week:
        writer.writerow([row["week_of"], row["custodians_live"]])
    writer.writerow([])

    writer.writerow(["Agent acceptance per custodian per day"])
    writer.writerow(["day", "custodian", "accepted"])
    for row in report.agent_acceptance_per_custodian_per_day:
        writer.writerow([row["day"], row["custodian"], row["accepted"]])
    writer.writerow([])

    writer.writerow(["Credits (real LLM cost) per custodian per day"])
    writer.writerow(["day", "custodian", "calls", "tokens_in", "tokens_out", "credits_usd"])
    for row in report.credits_per_custodian_per_day:
        writer.writerow([
            row["day"], row["custodian"], row["calls"],
            row["tokens_in"], row["tokens_out"], f"{row['credits_usd']:.4f}",
        ])

    return buffer.getvalue()


__all__ = [
    "DEFAULT_DAYS",
    "DEFAULT_WEEKS",
    "REPORT_TABLE",
    "ThroughputReport",
    "generate_report",
    "latest_report",
    "render_csv",
]
