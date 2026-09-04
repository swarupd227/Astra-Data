"""Versioned schema migrations for graph-svc.

Migrations are numbered Python modules in ``versions/``. Each declares:

``VERSION``
    Integer, unique, applied in ascending order.
``DESCRIPTION``
    One line, recorded in ``schema_migration``.
``ONTOLOGY_CHANGES``
    The breaking ontology changes this migration carries, each with the backfill that
    makes it safe. ``tools/migration_check.py`` refuses a breaking change in the schema
    that no migration claims here (S1.1.1 criterion 4).
``up(conn)``
    Async, receives an asyncpg connection already inside a transaction.

A hand-written runner rather than Alembic: almost all of this schema is Apache AGE DDL
(``create_graph``, ``create_vlabel``) rather than SQLAlchemy metadata, so an ORM-driven
autogenerate has nothing to work from, and the runner keeps the service image to asyncpg.
The trade-off is recorded in ADR 0001.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from types import ModuleType
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)

#: Held for the duration of a migration run so two replicas cannot migrate at once.
_ADVISORY_LOCK_KEY = 0x4153_5452  # "ASTR"

_SCHEMA_MIGRATION_DDL = """
CREATE TABLE IF NOT EXISTS public.schema_migration (
    version      integer PRIMARY KEY,
    description  text        NOT NULL,
    applied_at   timestamptz NOT NULL DEFAULT now()
)
"""


@dataclass(frozen=True, slots=True)
class OntologyChange:
    """A breaking ontology change a migration claims, with its backfill."""

    change: str
    """The change key from ``ontology.lock.diff`` — e.g.
    ``remove_property:node:Workbook.size``."""

    backfill: str
    """What this migration does to the data already in the graph. Must not be empty."""

    reason: str = ""


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    description: str
    ontology_changes: tuple[OntologyChange, ...]
    up: Callable[[asyncpg.Connection], Coroutine[Any, Any, None]]
    module: str


def _coerce_changes(raw: Any, module_name: str) -> tuple[OntologyChange, ...]:
    if not raw:
        return ()
    changes: list[OntologyChange] = []
    for entry in raw:
        if isinstance(entry, OntologyChange):
            change = entry
        elif isinstance(entry, dict):
            missing = {"change", "backfill"} - set(entry)
            if missing:
                raise ValueError(
                    f"{module_name}: ONTOLOGY_CHANGES entry is missing {sorted(missing)}"
                )
            change = OntologyChange(
                change=entry["change"],
                backfill=entry["backfill"],
                reason=entry.get("reason", ""),
            )
        else:
            raise TypeError(f"{module_name}: ONTOLOGY_CHANGES entries must be dicts")
        if not change.backfill.strip():
            raise ValueError(
                f"{module_name}: change {change.change!r} declares an empty backfill"
            )
        changes.append(change)
    return tuple(changes)


def _load(module: ModuleType) -> Migration:
    for attribute in ("VERSION", "DESCRIPTION", "up"):
        if not hasattr(module, attribute):
            raise ValueError(f"{module.__name__}: migration is missing {attribute}")
    return Migration(
        version=int(module.VERSION),
        description=str(module.DESCRIPTION),
        ontology_changes=_coerce_changes(getattr(module, "ONTOLOGY_CHANGES", ()), module.__name__),
        up=module.up,
        module=module.__name__,
    )


def discover() -> list[Migration]:
    """Every migration module, in ascending version order."""
    from . import versions

    found: list[Migration] = []
    for info in pkgutil.iter_modules(versions.__path__):
        if info.name.startswith("_"):
            continue
        found.append(_load(importlib.import_module(f"{versions.__name__}.{info.name}")))

    seen: dict[int, str] = {}
    for migration in found:
        if migration.version in seen:
            raise ValueError(
                f"migration version {migration.version} is declared by both "
                f"{seen[migration.version]} and {migration.module}"
            )
        seen[migration.version] = migration.module
    return sorted(found, key=lambda m: m.version)


async def applied_versions(conn: asyncpg.Connection) -> set[int]:
    await conn.execute(_SCHEMA_MIGRATION_DDL)
    rows = await conn.fetch("SELECT version FROM public.schema_migration")
    return {row["version"] for row in rows}


async def run(conn: asyncpg.Connection) -> list[Migration]:
    """Apply every pending migration. Returns the ones that were applied."""
    await conn.execute("SELECT pg_advisory_lock($1)", _ADVISORY_LOCK_KEY)
    try:
        already = await applied_versions(conn)
        applied: list[Migration] = []
        for migration in discover():
            if migration.version in already:
                continue
            logger.info(
                "applying migration %s: %s", migration.version, migration.description
            )
            async with conn.transaction():
                await migration.up(conn)
                await conn.execute(
                    "INSERT INTO public.schema_migration (version, description) VALUES ($1, $2)",
                    migration.version,
                    migration.description,
                )
            applied.append(migration)
        return applied
    finally:
        await conn.execute("SELECT pg_advisory_unlock($1)", _ADVISORY_LOCK_KEY)
