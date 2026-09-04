#!/usr/bin/env python
"""Migration guard.

S1.1.1 criterion 4: "Schema changes are versioned migrations; a migration that removes a
property fails CI unless it also supplies a backfill."

Compares the live ontology against ``ontology.lock.json`` and, for every *breaking*
change, requires a migration that claims it in ``ONTOLOGY_CHANGES`` with a non-empty
backfill. Additive changes need no migration.

``--write`` re-locks after the migration exists, which is the last step of a schema
change:

    1. change the ontology
    2. run this; it names the breaking changes and fails
    3. add a migration claiming each one, with its backfill
    4. run this again; it passes
    5. run with --write to update the lock file, and commit both
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Output carries the specification's own punctuation — em dashes, and arrows in edge
# endpoint pairs. A console on a legacy code page cannot encode those, and a guard that
# crashes while reporting a difference is worse than no guard.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


SERVICE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE_ROOT / "src"))

from astra_graph.migrations import discover  # noqa: E402
from astra_graph.ontology.lock import (  # noqa: E402
    LOCK_FILENAME,
    breaking,
    diff,
    read_lock,
    write_lock,
)
from astra_graph.ontology.registry import SCHEMA_VERSION  # noqa: E402

LOCK_PATH = SERVICE_ROOT / LOCK_FILENAME


def claimed_changes() -> dict[str, tuple[int, str]]:
    """Every change key claimed by a migration, mapped to (version, backfill)."""
    claimed: dict[str, tuple[int, str]] = {}
    for migration in discover():
        for change in migration.ontology_changes:
            claimed[change.change] = (migration.version, change.backfill)
    return claimed


def check() -> list[str]:
    if not LOCK_PATH.exists():
        return [
            f"{LOCK_FILENAME} does not exist. Create it with: "
            f"python tools/migration_check.py --write"
        ]

    locked = read_lock(LOCK_PATH)
    changes = diff(locked)
    problems: list[str] = []

    breaking_changes = breaking(changes)
    if breaking_changes:
        claimed = claimed_changes()
        for change in breaking_changes:
            if change.key() not in claimed:
                problems.append(
                    f"{change.describe()}\n"
                    f"      No migration claims this change. Add a migration under "
                    f"src/astra_graph/migrations/versions/ declaring:\n"
                    f"      ONTOLOGY_CHANGES = [{{'change': '{change.key()}', "
                    f"'backfill': '<what it does to existing data>'}}]"
                )

    locked_version = locked.get("schema_version")
    if changes and locked_version == SCHEMA_VERSION:
        problems.append(
            f"The ontology changed but SCHEMA_VERSION is still {SCHEMA_VERSION}. "
            f"Bump SCHEMA_VERSION in ontology/registry.py."
        )

    additive = [c for c in changes if not c.breaking]
    if additive and not problems:
        print("Additive ontology changes (no migration required):")
        for change in additive:
            print(f"  - {change.describe()}")

    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write", action="store_true", help="re-lock the ontology after migrations exist"
    )
    args = parser.parse_args()

    problems = check()
    if problems and not args.write:
        print("Ontology changes are not covered by a migration:\n", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    if args.write:
        write_lock(LOCK_PATH)
        print(f"locked ontology at schema version {SCHEMA_VERSION} → {LOCK_FILENAME}")
        return 0

    print(f"Migration guard passed: schema version {SCHEMA_VERSION} matches the lock file.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
