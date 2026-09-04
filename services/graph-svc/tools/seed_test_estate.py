#!/usr/bin/env python
"""Write a test estate through the real write path.

Used by the nightly replay job (S1.1.3 criterion 2 says the verification runs "on the test
estate") and useful by hand when working on the query API.

Everything goes through ``GraphWriter``, so every node and edge produces its mutation
event exactly as a harvest would. Some workbooks are retired, so the retirement path is
exercised too.

    python tools/seed_test_estate.py --workbooks 25
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


SERVICE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE_ROOT / "src"))

from astra_graph.config import settings  # noqa: E402
from astra_graph.events import source_for  # noqa: E402
from astra_graph.graph import AgeGraphRepository, create_pool  # noqa: E402
from astra_graph.ids import new_ulid  # noqa: E402
from astra_graph.logging_setup import configure_logging  # noqa: E402
from astra_graph.principal import Principal  # noqa: E402
from astra_graph.writes import EdgeWrite, GraphWriter, NodeWrite  # noqa: E402

#: Fan-out per workbook, shaped like the specification's §3.4 worked example.
SHEETS, DASHBOARDS, DATASOURCES, FIELDS, CALCS = 3, 1, 2, 6, 3

#: One workbook in this many is retired, so the nightly replay covers retirement.
RETIRE_EVERY = 8


async def seed(workbooks: int) -> tuple[int, int]:
    config = settings()
    pool = await create_pool(config)
    principal = Principal("agent:harvester", run_id=f"seed-{new_ulid()}")
    nodes = edges = 0

    try:
        repository = AgeGraphRepository(pool, graph_name=config.graph_name)
        writer = GraphWriter(repository, event_source=source_for(config.graph_name))

        site, project = await writer.write_nodes(
            [
                NodeWrite(type="Site", properties={"luid": f"site-{new_ulid()}", "name": "RQA"}),
                NodeWrite(
                    type="Project",
                    properties={"luid": f"proj-{new_ulid()}", "name": "Risk Core"},
                ),
            ],
            principal=principal,
        )
        nodes += 2
        await writer.write_edge(
            EdgeWrite(
                type="CONTAINS",
                from_id=site["properties"]["id"],
                to_id=project["properties"]["id"],
                properties={},
            ),
            principal=principal,
        )
        edges += 1

        for index in range(workbooks):
            created = await writer.write_nodes(
                _workbook_nodes(index), principal=principal
            )
            nodes += len(created)
            ids = [record["properties"]["id"] for record in created]
            written = await _link(writer, principal, project["properties"]["id"], ids)
            edges += written

            if index % RETIRE_EVERY == RETIRE_EVERY - 1:
                await writer.retire_node(
                    ids[-1],
                    reason="Retired by the test estate seeder to exercise the path",
                    principal=principal,
                )
    finally:
        await pool.close()
    return nodes, edges


def _workbook_nodes(index: int) -> list[NodeWrite]:
    suffix = new_ulid()
    out = [
        NodeWrite(
            type="Workbook",
            properties={
                "luid": f"wb-{suffix}",
                "name": f"Workbook {index}",
                "revision": str(index % 20 + 1),
                "views_90d": index * 7,
            },
        )
    ]
    out += [
        NodeWrite(
            type="Worksheet",
            properties={
                "name": f"Sheet {sheet}",
                "rows_shelf": ["Desk"],
                "cols_shelf": ["Date"],
                "marks_shelf": [],
            },
        )
        for sheet in range(SHEETS)
    ]
    out += [
        NodeWrite(
            type="Dashboard",
            properties={
                "name": f"Dashboard {dashboard}",
                "layout_json": {"zones": []},
                "contained_sheets": [f"Sheet {s}" for s in range(SHEETS)],
            },
        )
        for dashboard in range(DASHBOARDS)
    ]
    out += [
        NodeWrite(
            type="Datasource",
            properties={"name": f"Source {source}", "type": "published",
                        "luid": f"ds-{suffix}-{source}"},
        )
        for source in range(DATASOURCES)
    ]
    out += [
        NodeWrite(
            type="Field",
            properties={"name": f"Field {field}", "datatype": "real", "role": "measure"},
        )
        for field in range(FIELDS)
    ]
    out += [
        NodeWrite(
            type="CalculatedField",
            properties={
                "name": f"Calc {calc}",
                "formula": "SUM([A]) / SUM([B])",
                "formula_ast": {"op": "DIV"},
            },
        )
        for calc in range(CALCS)
    ]
    return out


async def _link(
    writer: GraphWriter, principal: Principal, project_id: str, ids: list[str]
) -> int:
    """Wire one workbook's nodes together. Returns the number of edges written."""
    offset = 0
    workbook = ids[offset]
    offset += 1
    sheets = ids[offset : offset + SHEETS]
    offset += SHEETS
    dashboards = ids[offset : offset + DASHBOARDS]
    offset += DASHBOARDS
    sources = ids[offset : offset + DATASOURCES]
    offset += DATASOURCES
    fields = ids[offset : offset + FIELDS]
    offset += FIELDS
    calcs = ids[offset : offset + CALCS]

    links: list[tuple[str, str, str, dict[str, object]]] = [
        ("CONTAINS", project_id, workbook, {}),
        *[("CONTAINS", workbook, sheet, {}) for sheet in sheets],
        *[("CONTAINS", workbook, dashboard, {}) for dashboard in dashboards],
        *[
            ("USES_DATASOURCE", sheet, source, {})
            for sheet in sheets
            for source in sources
        ],
        *[
            ("HAS_FIELD", sources[field % DATASOURCES], field_id, {})
            for field, field_id in enumerate(fields)
        ],
        *[("ENCODES", sheets[0], calc, {"shelf": "rows"}) for calc in calcs],
        *[
            ("DEPENDS_ON", calc, fields[0], {"position_in_ast": "args[0]"})
            for calc in calcs
        ],
    ]
    for edge_type, from_id, to_id, properties in links:
        await writer.write_edge(
            EdgeWrite(type=edge_type, from_id=from_id, to_id=to_id, properties=properties),
            principal=principal,
        )
    return len(links)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workbooks", type=int, default=10)
    args = parser.parse_args()

    configure_logging("WARNING")
    nodes, edges = asyncio.run(seed(args.workbooks))
    print(
        f"seeded {args.workbooks} workbooks into {settings().graph_name}: "
        f"{nodes} nodes, {edges} edges"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
