"""The "explain" affordance — story S10.1.2's own third AC: "every number on a screen has
an 'explain' affordance that opens the query or the events behind it."

**A registry of the real thing, not a description of it.** Every entry's own `text` is
copied verbatim from the module and line range its own `source` names — found by reading
the real implementation, not written fresh for this screen. A number computed by a SQL
query carries that query; a number computed by plain Python arithmetic over rows a query
already fetched (confirmed the more common shape here, by direct research across the
screens covered below) carries that arithmetic instead, honestly labelled `kind:
"computation"` rather than dressed up as a query it is not. Either way, the text a
platform engineer opens is the actual code deciding the number on their screen at that
moment — not a paraphrase that could drift from it the next time that code changes.

**"The events behind it" needs no new endpoint.** Every entry with a `subject_kind` names
the console's own existing `GET /v1/events?subject=<id>` (`routes.py`, the raw outbox —
`Api.subjectEvents` on the console side reads it directly); this module only needs to say
*which* real subject id a number is about, not re-implement reading its history.

**Coverage is a representative first pass, not literally every digit on every screen** —
the same disclosed-scope reading `App.tsx`'s own docstring already gave S10.1.1's "every
role" AC. Covering the flagship queues and boards this story's SSE AC already names
(Exception Desk, Regression Monitor, Wave Board, Programme Board) plus the Estate
Explorer and the G3 gate card gives every top-level surface at least one real, wired
example; the `Explain` console component and this registry are both generic, so adding
the next screen's own entry is one dict entry and one `<Explain metricKey="...">`, not new
infrastructure.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class ExplainEntry:
    metric_key: str
    title: str
    kind: Literal["sql", "computation"]
    text: str
    source: str
    subject_kind: str | None = None


EXPLAIN_REGISTRY: dict[str, ExplainEntry] = {
    "estate.total": ExplainEntry(
        metric_key="estate.total",
        title="Estate Explorer — workbook count",
        kind="computation",
        text=(
            "matching = [row for row in self.rows if where.matches(row)]\n"
            '"total": len(matching),\n'
            '"estate_total": len(self.rows),'
        ),
        source="astra_graph/estate.py:271-283 (Estate.page)",
    ),
    "exceptions.queue_count": ExplainEntry(
        metric_key="exceptions.queue_count",
        title="Exception Desk — cases in queue",
        kind="sql",
        text=(
            "SELECT id FROM {NODE_INDEX_TABLE}\n"
            " WHERE graph = $1 AND kind = 'node' AND label = 'ExceptionCase' "
            "AND retired_at IS NULL\n\n"
            "-- then, per row: only OPEN and BLOCKED cases are counted in the queue\n"
            "_QUEUE_STATES = {\"OPEN\", \"BLOCKED\"}"
        ),
        source="astra_graph/exception_desk.py:192,231-269 (queue)",
        subject_kind="exception_case",
    ),
    "regression.drift_unaddressed": ExplainEntry(
        metric_key="regression.drift_unaddressed",
        title="Regression Monitor — drift alert",
        kind="computation",
        text=(
            "SELECT subject, seq, time FROM public.estate_event\n"
            " WHERE graph = $1 AND type = $2 AND subject = ANY($3::text[])\n"
            " ORDER BY seq DESC\n\n"
            "drift_unaddressed = (\n"
            "    drift is not None and (schedule is None "
            "or drift['seq'] > schedule.last_seen_drift_seq)\n"
            ")"
        ),
        source="astra_graph/regression.py:748-800 (regression_monitor)",
        subject_kind="workbook",
    ),
    "programme.family_count": ExplainEntry(
        metric_key="programme.family_count",
        title="Programme Board — family count (planned / measured / delta)",
        kind="computation",
        text=(
            '"planned_family_count": PLANNED_FAMILY_COUNT,\n'
            '"family_count_delta": (\n'
            "    self.family_count - PLANNED_FAMILY_COUNT "
            "if self.family_count is not None else None\n"
            "),\n\n"
            "-- family_count itself is a stored, confirmed value:\n"
            "UPDATE {PROGRAMME_TABLE}\n"
            "   SET family_count = $3, family_count_confirmed_at = now(),\n"
            "       family_count_confirmed_by = $4\n"
            " WHERE graph = $1 AND id = $2 RETURNING *"
        ),
        source="astra_graph/retention.py:83-99,208-226 (Programme.as_dict, confirm_family_count)",
    ),
    "programme.class_mix": ExplainEntry(
        metric_key="programme.class_mix",
        title="Programme Board — calculation classes (C1/C2/C3/C4/Unclassified)",
        kind="computation",
        text=(
            "SELECT id FROM {NODE_INDEX_TABLE}\n"
            " WHERE graph = $1 AND kind = 'node' AND label = 'CalculatedField' "
            "AND retired_at IS NULL\n\n"
            "counts: dict[str, int] = dict.fromkeys(_CLASS_ORDER, 0)\n"
            "unclassified = 0\n"
            "for props in properties.values():\n"
            "    class_ = props.get('class')\n"
            "    if class_ in counts:\n"
            "        counts[class_] += 1\n"
            "    else:\n"
            "        unclassified += 1"
        ),
        source="astra_graph/classify.py:434-477 (class_mix)",
    ),
    "programme.rule_coverage": ExplainEntry(
        metric_key="programme.rule_coverage",
        title="Programme Board — rule coverage",
        kind="computation",
        text=(
            "SELECT id FROM {NODE_INDEX_TABLE}\n"
            " WHERE graph = $1 AND kind = 'node' AND label = 'CalculatedField' "
            "AND retired_at IS NULL\n"
            "-- then children(conn, graph_name, calc_ids, 'MAPS_TO', 'Measure')\n\n"
            "total = len(calc_ids)\n"
            "matched = len(measures)\n"
            "\"percentage\": round(matched / total * 100, 1) if total else 0.0"
        ),
        source="astra_graph/rules.py:769-800 (rule_coverage)",
    ),
    "invoicing.accepted_by_tier": ExplainEntry(
        metric_key="invoicing.accepted_by_tier",
        title="Programme Board — accepted units by tier",
        kind="sql",
        text=(
            "SELECT tier, count(*) AS n FROM {LEDGER_TABLE} "
            "WHERE graph = $1 GROUP BY tier\n\n"
            "-- combined with the disclosed PLANNED_BY_TIER constant and the live unit "
            "price:\n"
            "\"delta\": accepted[tier] - PLANNED_BY_TIER[tier],\n"
            "\"accepted_value\": accepted[tier] * prices[tier]"
        ),
        source="astra_graph/invoicing.py:209-238 (accepted_by_tier, programme_acceptance_summary)",
    ),
    "trains.wip_status": ExplainEntry(
        metric_key="trains.wip_status",
        title="Wave Board — train member count and WIP",
        kind="sql",
        text=(
            "SELECT e.id AS edge_id, e.from_id AS workbook, e.to_id AS train\n"
            "  FROM {EDGE_INDEX_TABLE} e\n"
            "  JOIN {NODE_INDEX_TABLE} n ON n.id = e.from_id AND n.kind = 'node'\n"
            "   AND n.graph = $1 AND n.label = 'Workbook' AND n.retired_at IS NULL\n"
            " WHERE e.graph = $1 AND e.label = 'IN_TRAIN' AND e.to_id = ANY($2::text[])\n"
            "   AND e.retired_at IS NULL"
        ),
        source="astra_graph/trains.py:340-404 (_train_members, _train_summary)",
        subject_kind="train",
    ),
    "g3.parity_cases": ExplainEntry(
        metric_key="g3.parity_cases",
        title="G3 Gate Card — parity cases run/pass",
        kind="computation",
        text=(
            "cases_run = sum(int(sheet.get('cases_run') or 0) for sheet in dashboard['sheets'])\n"
            "cases_pass = sum(int(sheet.get('pass') or 0) for sheet in dashboard['sheets'])"
        ),
        source="astra_graph/g3_card.py:203-239 (_proof)",
        subject_kind="workbook",
    ),
}
