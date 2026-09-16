"""Throughput and cost metrics -- story S6.2.3, WBS-numbered in its own backlog entry
but otherwise unrelated to `E6. Compositor`'s own `S6.2.1`/`S6.2.2` (see migration
v0045's own docstring for the full research trail).

    "As a project manager, I want custodians live per week, agent acceptance and
    credits per custodian per day, so that reporting is generated."

**Three real, disclosed translations, each confirmed by the user before any code was
written** (this story's own vocabulary appears nowhere else in this codebase):

- **"Custodian" = this platform's own `Site` node.** Every function below reports by
  `site_id`, hydrated to the site's own real `name` for display.
- **"Credits" = real LLM token cost**, `gateway.token_cost_usd`'s own real, disclosed,
  per-provider rate applied to `gateway_request_log`'s own real `tokens_in`/
  `tokens_out` (S6.2.3's own migration, v0045).
- **"Agent acceptance" = the existing `commercial_ledger`/`invoicing.accepted_by_tier`
  fact** (an MU reaching `ACCEPTED` at G3, story S9.1.2) -- no second acceptance
  concept invented; a workbook's own real site is resolved by importing `release.
  _sites_for_workbooks` directly (cross-epic private reuse, the identical convention
  that module's own import of `foundry_routing._family_for_workbook` already set,
  rather than re-declaring the identical two-hop `CONTAINS` join a second time).

**"Cost per custodian visible from query tags"** -- `query_tag` (`gateway._dispatch`'s
own new parameter) is the tag: a caller attaches the real site id it already resolved
for its own workbook/calc, and `credits_per_custodian_per_day` below is nothing more
than grouping `gateway_request_log` by that tag and by day. A row with no `query_tag`
(a call from a caller that has not been updated to attach one, or one made before this
story) is honestly reported under `None` rather than silently dropped or guessed at.

**A real, disclosed gap: `MENDER_REPAIR` is still not routable in this deployment**
(`gateway.py`'s own module docstring, story S8.2.1) -- `mender.py`'s own real call site
resolves and passes a real `query_tag` regardless, so the mechanism is correct and
ready the moment that task class becomes routable, but today's real, live query-tagged
dispatches come from `TRANSPILE_C3`/`TRANSPILE_C3_SMALL_MODEL` alone.

**"Custodians live per week"** -- a site counts as live in a given ISO week iff it has
real evidence of migration activity that week: a query-tagged gateway dispatch, or an
MU acceptance, attributed to it. Tying "live" to the identical two facts the other two
metrics already report keeps all three numbers self-consistent, rather than inventing
a fourth, separate liveness signal (e.g. harvest recency) nothing else in this report
uses.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import asyncpg

from .invoicing import LEDGER_TABLE
from .lineage import hydrate
from .release import _sites_for_workbooks  # cross-epic private reuse; see module docstring

GATEWAY_REQUEST_LOG_TABLE = "public.gateway_request_log"


async def _site_names(pool: asyncpg.Pool, graph_name: str, site_ids: Sequence[str]) -> dict[str, str]:
    wanted = [s for s in dict.fromkeys(site_ids) if s]
    if not wanted:
        return {}
    async with pool.acquire() as conn:
        sites = await hydrate(conn, graph_name, "Site", wanted)
    return {site_id: str(props.get("name") or site_id) for site_id, props in sites.items()}


def _custodian_label(site_id: str | None, names: dict[str, str]) -> str:
    if site_id is None:
        return "(unattributed)"
    return names.get(site_id, site_id)


async def custodians_live_per_week(
    pool: asyncpg.Pool, graph_name: str, *, weeks: int = 12
) -> list[dict[str, Any]]:
    """Distinct custodians (sites) with real activity in each of the trailing `weeks`
    ISO weeks -- "live" means a query-tagged gateway dispatch or an MU acceptance
    attributed to that site landed in that week (this module's own docstring explains
    why no separate liveness signal is used)."""
    async with pool.acquire() as conn:
        gateway_rows = await conn.fetch(
            f"""
            SELECT date_trunc('week', created_at) AS week_of, query_tag AS site_id
              FROM {GATEWAY_REQUEST_LOG_TABLE}
             WHERE graph = $1 AND query_tag IS NOT NULL
               AND created_at >= now() - ($2 || ' weeks')::interval
            """,
            graph_name, str(weeks),
        )
        ledger_rows = await conn.fetch(
            f"""
            SELECT date_trunc('week', recorded_at) AS week_of, workbook_id
              FROM {LEDGER_TABLE}
             WHERE graph = $1 AND recorded_at >= now() - ($2 || ' weeks')::interval
            """,
            graph_name, str(weeks),
        )

    workbook_ids = sorted({row["workbook_id"] for row in ledger_rows})
    sites_for_workbook = await _sites_for_workbooks(pool, graph_name, workbook_ids)

    by_week: dict[Any, set[str]] = {}
    for row in gateway_rows:
        by_week.setdefault(row["week_of"], set()).add(row["site_id"])
    for row in ledger_rows:
        site_id = sites_for_workbook.get(row["workbook_id"])
        if site_id:
            by_week.setdefault(row["week_of"], set()).add(site_id)

    return [
        {"week_of": week_of.date().isoformat(), "custodians_live": len(site_ids)}
        for week_of, site_ids in sorted(by_week.items())
    ]


async def agent_acceptance_per_custodian_per_day(
    pool: asyncpg.Pool, graph_name: str, *, days: int = 30
) -> list[dict[str, Any]]:
    """Real MU acceptances (`commercial_ledger`, story S9.1.2) over the trailing `days`
    days, attributed to each accepted workbook's own real site and grouped by day. A
    workbook whose site cannot be resolved (never harvested with a real `CONTAINS`
    chain to a Site) is honestly reported under `None`, never dropped silently."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT date_trunc('day', recorded_at) AS day, workbook_id
              FROM {LEDGER_TABLE}
             WHERE graph = $1 AND recorded_at >= now() - ($2 || ' days')::interval
            """,
            graph_name, str(days),
        )

    workbook_ids = sorted({row["workbook_id"] for row in rows})
    sites_for_workbook = await _sites_for_workbooks(pool, graph_name, workbook_ids)
    site_ids = {sites_for_workbook.get(wb) for wb in workbook_ids}
    names = await _site_names(pool, graph_name, [s for s in site_ids if s])

    counts: dict[tuple[Any, str | None], int] = {}
    for row in rows:
        site_id = sites_for_workbook.get(row["workbook_id"])
        key = (row["day"], site_id)
        counts[key] = counts.get(key, 0) + 1

    return [
        {
            "day": day.date().isoformat(),
            "site_id": site_id,
            "custodian": _custodian_label(site_id, names),
            "accepted": count,
        }
        for (day, site_id), count in sorted(counts.items(), key=lambda item: (item[0][0], item[0][1] or ""))
    ]


async def credits_per_custodian_per_day(
    pool: asyncpg.Pool, graph_name: str, *, days: int = 30
) -> list[dict[str, Any]]:
    """Real token counts and their own real, computed `cost_usd` (`gateway.
    token_cost_usd`) over the trailing `days` days, grouped by each real call's own
    `query_tag` (the AC's own "query tags") and by day. A row logged before this story,
    or by a caller that has not attached a tag, is honestly reported under `None`."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT date_trunc('day', created_at) AS day, query_tag AS site_id,
                   COALESCE(SUM(tokens_in), 0) AS tokens_in,
                   COALESCE(SUM(tokens_out), 0) AS tokens_out,
                   COALESCE(SUM(cost_usd), 0) AS cost_usd,
                   count(*) AS calls
              FROM {GATEWAY_REQUEST_LOG_TABLE}
             WHERE graph = $1 AND created_at >= now() - ($2 || ' days')::interval
             GROUP BY day, query_tag
            """,
            graph_name, str(days),
        )

    site_ids = [row["site_id"] for row in rows if row["site_id"]]
    names = await _site_names(pool, graph_name, site_ids)

    return [
        {
            "day": row["day"].date().isoformat(),
            "site_id": row["site_id"],
            "custodian": _custodian_label(row["site_id"], names),
            "calls": int(row["calls"]),
            "tokens_in": int(row["tokens_in"]),
            "tokens_out": int(row["tokens_out"]),
            "credits_usd": float(row["cost_usd"]),
        }
        for row in sorted(rows, key=lambda r: (r["day"], r["site_id"] or ""))
    ]


__all__ = [
    "GATEWAY_REQUEST_LOG_TABLE",
    "agent_acceptance_per_custodian_per_day",
    "credits_per_custodian_per_day",
    "custodians_live_per_week",
]
