"""Conformance rules enforced at emission — story S4.3.2.

    "As an architect, I want conformance rules enforced at emission, so that no model
    reaches the client repository that breaks the target architecture."

**§12.3, transcribed, with one substitution.** The spec names six checks: star schema (no
many-to-many without a bridge), a single active relationship path between any two tables,
conformed dimensions shared by reference not copied, measures in display folders by source
family, every column carrying a (drafted, ASSISTED) description, and RLS roles tested with
a fixture user. The backlog's own acceptance criteria drops the column-description check
and adds "naming convention" instead — and the substitution is not arbitrary: no
`Field`/column data has ever been threaded into `design_document` (`tmdl.py`'s own disclosed
gap, S4.3.1), so §12.3's column-description rule names an input this platform genuinely
cannot harvest today, while a naming-convention check over tables/measures/roles the design
document already carries is real and checkable now — the same "spec wins unless the
backlog answers with real data the spec's own version can't reach" precedent this codebase
has followed since S4.2.x. Both gaps (this substitution, and the dropped rule) are recorded
here rather than silently reconciled.

**Every rule is a pure function of the frozen design document** — the same purity
discipline `tmdl.emit_tmdl` already established, for the identical reason: a conformance
verdict must be reproducible from a version alone, not from whatever the graph happens to
say when a build happens to run.

**Rules are data — a versioned, admin-editable ruleset, not a code change per rule
flip.** `public.conformance_ruleset` (migration v0019) holds one row per saved version,
mirroring `g2_question`/`g2_reminder`/`build_run`'s own "history, not a mutable row"
footing: an architect's edit is a new version, never an overwrite, so a build recorded
against version 3 stays checkable against exactly what version 3 said even after version 4
ships. `RULES` (the Python callables) and `RULE_METADATA` (label/description/param schema,
for the Admin screen to render without guessing) are still code — what is genuinely *data*
is which rules are enabled and their parameters (`naming_convention.max_length`,
`rls_fixture_user.fixture_username`), the same "declare the mechanism in code, let an
operator tune the knobs" split `harvest.PromotionGate` already draws between a fixed gate
shape and an administered promotion record.

**Two structural checks share one representation: relationships as a table-id edge list.**
`design_document["relationships"]` (S4.1.1) is already a directed edge list —
`from_table`/`to_table`/`cardinality` — so both `star_schema` (an unresolved, ambiguous
cardinality is treated as an unconfirmed many-to-many, since this platform has no primary-
key metadata to call it anything else — `modeller.infer_cardinality`'s own reasoning) and
`single_active_path` (a union-find cycle check over the *undirected* graph: a star or
snowflake is a tree and has none; a redundant join back to an already-reachable table
does — the same shape Power BI's own "only one relationship may be active" rule polices)
read the identical edge list for two different structural properties.

**Conformed dimensions "by reference, not copied" is checked against storage mode.**
`ModelTable.mode == "import"` means the table's data is physically copied into this model;
a dimension `conformed_dimensions` (S3.1.1) already names as shared with other families
being imported here contradicts "shared by reference" directly and checkably, without this
platform needing to build the real cross-family reference mechanism first.

**Measures "in display folders by source family" is checked as a name-collision guard.**
Every candidate measure in one build already lands in one family-named display folder
(`tmdl._measures_file`, `displayFolder` set to `document["family_name"]`) — assigning the
folder itself cannot fail, since emission does it unconditionally. What *can* genuinely
fail, and what the folder-by-family requirement exists to prevent, is two measures
colliding by name within it; that is what this rule checks.

**RLS "tested with a fixture user" is a structural smoke check, the same honesty
`FixtureTargetAdapter.smoke_query` (S4.3.1) already applies.** No live analysis engine
exists to evaluate a filter expression for real, and the expression itself is still
Tableau-syntax, not DAX (the Transpiler, E5, is what would translate it) — so "tested" here
means: the expression names a field, calls a recognised user-context function
(`USERNAME`/`ISMEMBEROF`/`FULLNAME`), and is therefore *evaluable* once a fixture identity
is substituted in, not that it was actually evaluated against one.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

import asyncpg

from .ids import new_ulid

RULESET_TABLE = "public.conformance_ruleset"

_JOIN_TABLE_RE = re.compile(r"^\s*[\w.]+\s*\.\s*\w+\s*=\s*[\w.]+\s*\.\s*\w+\s*$")
_RLS_FIELD_RE = re.compile(r"\[[^\]]+\]")
_RLS_FUNCTIONS = ("USERNAME", "ISMEMBEROF", "FULLNAME")


@dataclass(frozen=True, slots=True)
class Violation:
    rule_id: str
    object_ref: str
    message: str

    def as_dict(self) -> dict[str, Any]:
        return {"rule_id": self.rule_id, "object_ref": self.object_ref, "message": self.message}

    def __str__(self) -> str:
        return f"{self.object_ref}: {self.message}"


# ---------------------------------------------------------------------------- rule checks


def check_star_schema(document: Mapping[str, Any], params: Mapping[str, Any]) -> list[Violation]:
    tables_by_id = {t.get("id"): t.get("name") for t in document.get("tables") or []}
    violations = []
    for rel in document.get("relationships") or []:
        if rel.get("cardinality") is not None:
            continue
        from_name = tables_by_id.get(rel.get("from_table"), rel.get("from_table"))
        to_name = tables_by_id.get(rel.get("to_table"), rel.get("to_table"))
        violations.append(
            Violation(
                rule_id="star_schema",
                object_ref=f"{from_name} ↔ {to_name}",
                message=(
                    "cardinality is unresolved (row estimates too close to call) — an "
                    "unconfirmed many-to-many relationship with no bridge table"
                ),
            )
        )
    return violations


def check_single_active_path(document: Mapping[str, Any], params: Mapping[str, Any]) -> list[Violation]:
    tables_by_id = {t.get("id"): t.get("name") for t in document.get("tables") or []}
    parent: dict[str, str] = {}

    def find(node: str) -> str:
        root = node
        while parent.get(root, root) != root:
            root = parent[root]
        return root

    violations = []
    for rel in document.get("relationships") or []:
        a, b = rel.get("from_table"), rel.get("to_table")
        if a is None or b is None:
            continue
        root_a, root_b = find(a), find(b)
        if root_a == root_b:
            violations.append(
                Violation(
                    rule_id="single_active_path",
                    object_ref=f"{tables_by_id.get(a, a)} ↔ {tables_by_id.get(b, b)}",
                    message=(
                        "this relationship creates a second path between two tables "
                        "already connected through others — only one active path is allowed"
                    ),
                )
            )
        else:
            parent[root_a] = root_b
    return violations


def check_conformed_dimensions_by_reference(
    document: Mapping[str, Any], params: Mapping[str, Any]
) -> list[Violation]:
    tables_by_name: dict[str, list[Mapping[str, Any]]] = {}
    for table in document.get("tables") or []:
        name = table.get("name")
        if name:
            tables_by_name.setdefault(str(name).lower(), []).append(table)

    violations = []
    for dimension in document.get("conformed_dimensions") or []:
        shared_with = dimension.get("shared_with_family_ids") or []
        if not shared_with:
            continue
        for table in tables_by_name.get(str(dimension.get("dimension") or "").lower(), []):
            if table.get("mode") == "import":
                violations.append(
                    Violation(
                        rule_id="conformed_dimensions_by_reference",
                        object_ref=str(table.get("name") or table.get("id")),
                        message=(
                            f"'{dimension.get('dimension')}' is shared with {len(shared_with)} "
                            f"other family/families but is imported (copied) here rather than "
                            f"shared by reference"
                        ),
                    )
                )
    return violations


def check_measures_display_folder(
    document: Mapping[str, Any], params: Mapping[str, Any]
) -> list[Violation]:
    seen: dict[str, str] = {}
    violations = []
    for measure in document.get("candidate_measures") or []:
        name = str(measure.get("name") or "")
        key = name.strip().lower()
        if not key:
            continue
        if key in seen:
            violations.append(
                Violation(
                    rule_id="measures_display_folder",
                    object_ref=name,
                    message=(
                        f"duplicates the name of another measure ('{seen[key]}') in this "
                        f"family's own display folder"
                    ),
                )
            )
        else:
            seen[key] = name
    return violations


def check_naming_convention(document: Mapping[str, Any], params: Mapping[str, Any]) -> list[Violation]:
    raw_max_length = params.get("max_length")
    max_length = int(raw_max_length) if raw_max_length is not None else 100
    violations: list[Violation] = []

    def check_name(kind: str, name: Any) -> None:
        if name is None:
            return
        text = str(name)
        ref = text if text.strip() else "(blank)"
        if not text.strip():
            violations.append(Violation("naming_convention", ref, f"{kind} name is blank"))
            return
        if text != text.strip():
            violations.append(Violation("naming_convention", ref, f"{kind} name has leading/trailing whitespace"))
        if len(text) > max_length:
            violations.append(
                Violation("naming_convention", ref, f"{kind} name exceeds {max_length} characters")
            )
        if text[0].isdigit():
            violations.append(Violation("naming_convention", ref, f"{kind} name starts with a digit"))
        if '"' in text:
            violations.append(
                Violation("naming_convention", ref, f"{kind} name contains a double quote (unsafe in TMDL)")
            )

    for table in document.get("tables") or []:
        check_name("table", table.get("name"))
    for measure in document.get("candidate_measures") or []:
        check_name("measure", measure.get("name"))
    for role in document.get("rls_role_detail") or []:
        check_name("role", role.get("name"))
    return violations


def check_rls_fixture_user(document: Mapping[str, Any], params: Mapping[str, Any]) -> list[Violation]:
    fixture_username = str(params.get("fixture_username") or "fixture.user@astra.local")
    violations = []
    for role in document.get("rls_role_detail") or []:
        name = str(role.get("name") or "(unnamed role)")
        expression = str(role.get("expression") or "")
        if not expression.strip():
            violations.append(
                Violation("rls_fixture_user", name, "no filter expression to test against a fixture user")
            )
            continue
        if not _RLS_FIELD_RE.search(expression):
            violations.append(
                Violation(
                    "rls_fixture_user", name,
                    f"expression {expression!r} names no field — cannot be evaluated for {fixture_username}",
                )
            )
            continue
        if not any(fn in expression.upper() for fn in _RLS_FUNCTIONS):
            violations.append(
                Violation(
                    "rls_fixture_user", name,
                    f"expression {expression!r} calls no recognised user-context function "
                    f"({', '.join(_RLS_FUNCTIONS)}) — cannot be tested against a fixture user",
                )
            )
    return violations


RULES: dict[str, Callable[[Mapping[str, Any], Mapping[str, Any]], list[Violation]]] = {
    "star_schema": check_star_schema,
    "single_active_path": check_single_active_path,
    "conformed_dimensions_by_reference": check_conformed_dimensions_by_reference,
    "measures_display_folder": check_measures_display_folder,
    "naming_convention": check_naming_convention,
    "rls_fixture_user": check_rls_fixture_user,
}

#: Label, description and (where a rule has one) its editable parameters — everything the
#: Admin screen needs to render every rule without the console guessing at its shape.
RULE_METADATA: dict[str, dict[str, Any]] = {
    "star_schema": {
        "label": "Star schema only",
        "description": "No many-to-many relationship without a bridge table (§12.3).",
        "params": {},
    },
    "single_active_path": {
        "label": "Single active relationship path",
        "description": "No two tables may be reachable by more than one relationship path (§12.3).",
        "params": {},
    },
    "conformed_dimensions_by_reference": {
        "label": "Conformed dimensions shared by reference",
        "description": "A dimension shared with other families must not be imported (copied) here (§12.3).",
        "params": {},
    },
    "measures_display_folder": {
        "label": "Measures in display folders by source family",
        "description": "Every measure name must be unique within its family's own display folder (§12.3).",
        "params": {},
    },
    "naming_convention": {
        "label": "Naming convention",
        "description": "Table, measure and role names must be non-blank, untrimmed-safe and TMDL-safe.",
        "params": {"max_length": "Maximum name length (characters)"},
    },
    "rls_fixture_user": {
        "label": "RLS roles tested with a fixture user",
        "description": "Every RLS role's expression must name a field and a recognised user-context function (§12.3).",
        "params": {"fixture_username": "The fixture identity roles are nominally tested against"},
    },
}


def check_conformance(
    document: Mapping[str, Any], ruleset: "ConformanceRuleset"
) -> list[Violation]:
    """Every enabled rule in ``ruleset``, run against the frozen design document."""
    violations: list[Violation] = []
    for rule in ruleset.rules:
        if not rule.enabled:
            continue
        check = RULES.get(rule.rule_id)
        if check is None:
            continue
        violations.extend(check(document, rule.params))
    return violations


# ------------------------------------------------------------------------------- ruleset


@dataclass(frozen=True, slots=True)
class RuleConfig:
    rule_id: str
    enabled: bool = True
    params: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"rule_id": self.rule_id, "enabled": self.enabled, "params": dict(self.params)}


#: The floor every graph starts with until an architect saves one of their own — version 0,
#: never persisted, so a fresh deployment can build immediately rather than refusing every
#: family until someone visits Admin first.
DEFAULT_RULES: tuple[RuleConfig, ...] = (
    RuleConfig("star_schema", True),
    RuleConfig("single_active_path", True),
    RuleConfig("conformed_dimensions_by_reference", True),
    RuleConfig("measures_display_folder", True),
    RuleConfig("naming_convention", True, {"max_length": 100}),
    RuleConfig("rls_fixture_user", True, {"fixture_username": "fixture.user@astra.local"}),
)


@dataclass(frozen=True, slots=True)
class ConformanceRuleset:
    version: int
    rules: tuple[RuleConfig, ...]
    updated_by: str
    updated_at: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "rules": [r.as_dict() for r in self.rules],
            "updated_by": self.updated_by,
            "updated_at": self.updated_at,
        }


_DEFAULT_RULESET = ConformanceRuleset(
    version=0, rules=DEFAULT_RULES, updated_by="system", updated_at=None
)


class ConformanceRulesetStore(Protocol):
    async def latest(self) -> ConformanceRuleset: ...

    async def save(self, rules: Sequence[RuleConfig], *, updated_by: str) -> ConformanceRuleset: ...


class PostgresConformanceRulesetStore:
    def __init__(self, pool: asyncpg.Pool, *, graph_name: str) -> None:
        self._pool = pool
        self._graph = graph_name

    async def latest(self) -> ConformanceRuleset:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT * FROM {RULESET_TABLE} WHERE graph = $1 ORDER BY version DESC LIMIT 1",
                self._graph,
            )
        return _from_row(row) if row else _DEFAULT_RULESET

    async def save(self, rules: Sequence[RuleConfig], *, updated_by: str) -> ConformanceRuleset:
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                current = await conn.fetchval(
                    f"SELECT MAX(version) FROM {RULESET_TABLE} WHERE graph = $1", self._graph,
                )
                version = (current or 0) + 1
                row = await conn.fetchrow(
                    f"""
                    INSERT INTO {RULESET_TABLE} (id, graph, version, rules, updated_by, updated_at)
                    VALUES ($1, $2, $3, $4::jsonb, $5, now())
                 RETURNING *
                    """,
                    f"ruleset_{new_ulid()}",
                    self._graph,
                    version,
                    json.dumps([r.as_dict() for r in rules]),
                    updated_by,
                )
        assert row is not None
        return _from_row(row)


def _from_row(row: asyncpg.Record) -> ConformanceRuleset:
    rules_raw = row["rules"]
    rules_list = json.loads(rules_raw) if isinstance(rules_raw, str) else list(rules_raw)
    updated_at = row["updated_at"]
    return ConformanceRuleset(
        version=row["version"],
        rules=tuple(RuleConfig(**r) for r in rules_list),
        updated_by=row["updated_by"],
        updated_at=updated_at.isoformat() if updated_at else None,
    )


__all__ = [
    "DEFAULT_RULES",
    "RULES",
    "RULESET_TABLE",
    "RULE_METADATA",
    "ConformanceRuleset",
    "ConformanceRulesetStore",
    "PostgresConformanceRulesetStore",
    "RuleConfig",
    "Violation",
    "check_conformance",
    "check_conformed_dimensions_by_reference",
    "check_measures_display_folder",
    "check_naming_convention",
    "check_rls_fixture_user",
    "check_single_active_path",
    "check_star_schema",
]
