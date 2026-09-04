"""The guards that keep the schema, the generated reference and the specification aligned.

S1.1.1 criteria 3 and 4. These run the same code paths CI runs, so a failure here is the
failure a pull request would get.
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from astra_graph.migrations import discover
from astra_graph.ontology.lock import LOCK_FILENAME, breaking, diff, read_lock, snapshot
from astra_graph.ontology.registry import SCHEMA_VERSION

SERVICE_ROOT = Path(__file__).resolve().parents[1]
TOOLS = SERVICE_ROOT / "tools"


def _run(tool: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(TOOLS / tool), *args],
        capture_output=True,
        text=True,
        cwd=SERVICE_ROOT,
    )


# ------------------------------------------------------- generated reference and spec


def test_generated_ontology_reference_is_current() -> None:
    result = _run("ontology_check.py", "--generated")
    assert result.returncode == 0, result.stdout + result.stderr


def test_schema_matches_the_specification() -> None:
    """Every node and edge type in §4.1.1/§4.1.2, with the same property names, or a
    declared deviation carrying a reason."""
    result = _run("ontology_check.py", "--spec")
    assert result.returncode == 0, result.stdout + result.stderr


# ------------------------------------------------------------------- migration guard


def test_lock_file_matches_the_live_schema() -> None:
    locked = read_lock(SERVICE_ROOT / LOCK_FILENAME)
    assert diff(locked) == [], (
        "the ontology has changed since it was locked; run "
        "`python tools/migration_check.py` and follow what it says"
    )


def test_migration_guard_passes_on_the_committed_schema() -> None:
    result = _run("migration_check.py")
    assert result.returncode == 0, result.stdout + result.stderr


def test_lock_records_the_current_schema_version() -> None:
    locked = read_lock(SERVICE_ROOT / LOCK_FILENAME)
    assert locked["schema_version"] == SCHEMA_VERSION


def test_removing_a_property_is_classified_as_breaking() -> None:
    """The guard's job: a removed property is not an additive change."""
    locked = copy.deepcopy(snapshot())
    live_workbook = next(
        p for p in locked["nodes"]["Workbook"]["properties"] if p["name"] == "size"
    )
    locked["nodes"]["Workbook"]["properties"] = [
        p for p in locked["nodes"]["Workbook"]["properties"] if p["name"] != "size"
    ]
    # 'size' is now in the live schema but not the lock: an addition, which is additive.
    additions = diff(locked)
    assert [c.key() for c in additions] == ["add_property:node:Workbook.size"]
    assert breaking(additions) == []

    # Reverse the comparison: the lock has a property the live schema does not.
    locked["nodes"]["Workbook"]["properties"].append(live_workbook)
    locked["nodes"]["Workbook"]["properties"].append(
        {"name": "retired_column", "type": "string", "required": False, "enum": None,
         "server_managed": False}
    )
    removals = breaking(diff(locked))
    assert [c.key() for c in removals] == ["remove_property:node:Workbook.retired_column"]
    assert "was removed" in removals[0].detail


@pytest.mark.parametrize(
    ("mutate", "expected_kind"),
    [
        (lambda s: s["nodes"].pop("Wave"), "add_node_type"),
        (lambda s: s["edges"].pop("PROVED_BY"), "add_edge_type"),
    ],
)
def test_added_types_are_additive(mutate, expected_kind) -> None:
    locked = copy.deepcopy(snapshot())
    mutate(locked)
    changes = diff(locked)
    assert all(not c.breaking for c in changes)
    assert any(c.kind == expected_kind for c in changes)


def test_making_a_property_required_is_breaking() -> None:
    locked = copy.deepcopy(snapshot())
    for prop in locked["nodes"]["Workbook"]["properties"]:
        if prop["name"] == "revision":
            prop["required"] = False
    changes = breaking(diff(locked))
    assert [c.key() for c in changes] == ["require_property:node:Workbook.revision"]


def test_dropping_an_enum_value_is_breaking() -> None:
    locked = copy.deepcopy(snapshot())
    for prop in locked["nodes"]["Verdict"]["properties"]:
        if prop["name"] == "result":
            prop["enum"] = [*prop["enum"], "SKIPPED"]
    changes = breaking(diff(locked))
    assert [c.key() for c in changes] == ["remove_enum_value:node:Verdict.result"]


def test_withdrawing_an_edge_pair_is_breaking() -> None:
    locked = copy.deepcopy(snapshot())
    locked["edges"]["CONTAINS"]["pairs"].append(["Site", "Workbook"])
    changes = breaking(diff(locked))
    assert [c.key() for c in changes] == ["remove_edge_pair:edge:CONTAINS"]


# --------------------------------------------------------------------- migrations


def test_migrations_are_uniquely_versioned_and_ordered() -> None:
    migrations = discover()
    assert migrations
    versions = [m.version for m in migrations]
    assert versions == sorted(versions)
    assert len(set(versions)) == len(versions)


def test_every_declared_ontology_change_carries_a_backfill() -> None:
    for migration in discover():
        for change in migration.ontology_changes:
            assert change.backfill.strip(), (
                f"migration {migration.version} claims {change.change} with no backfill"
            )


def test_lock_file_is_deterministic() -> None:
    first = json.dumps(snapshot(), sort_keys=True)
    second = json.dumps(snapshot(), sort_keys=True)
    assert first == second
