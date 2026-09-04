"""Release trains — story S3.2.1.

    "As a programme manager, I want release trains proposed from families and usage, so
    that the plan is grouped by what has to be designed together, not by site."

§3.3: "A release train is an ordered group of MUs that share model families and move
through generation, proof, acceptance and release together. Trains are proposed by the
Cartographer from graph clustering and confirmed by the Programme Manager... Sequencing by
shared model rather than by site is a deliberate choice: it means each model family is
designed and approved once, and later MUs in a train benefit from the patterns and
adjudications recorded on earlier ones."

**There is no Migration Unit graph node.** §4.1.1's own note on the Workbook node type is
"One Migration Unit per Workbook" — so "packing MUs" here means packing Workbooks, and
``IN_TRAIN`` (already declared, ``Workbook -> ReleaseTrain``, §4.1.2, carrying a required
``sequence``) is the edge this module writes. A real MU control-plane record (§3.1, state
machine §3.2) is a later story's job — ``migration_units.py``'s own docstring already says
so ("Correct until E3 creates the first one").

**Ordering factors are the backlog's own three, not reworded to match §8.5.** §8.5
describes the Cartographer's train cost function as preferring "high reuse, high usage and
early-renewal sites" — the third factor needs a site licence-renewal date, and nothing this
platform harvests (§4.1.1's Site node, the Metadata API, the licence export) carries one.
The backlog's named factors for this story — shared model readiness, usage, tier mix — are
implemented here instead, because they are both this story's literal acceptance criterion
and the only three factors this graph can actually answer today. See ADR 0025.

- **Shared model readiness** = a family's position in its own §12.2 lifecycle
  (``_READINESS_RANK``): SINGLETON and PROPOSED rank equally (neither has been through G2),
  DRAFT through PUBLISHED rank increasingly ready.
- **Usage** = the sum of ``Workbook.views_90d`` (S1.2.3) across a family's members.
- **Tier mix** = the mean tier complexity (SIMPLE < MODERATE < COMPLEX < REDESIGN,
  S1.4.1's scope decisions) across whichever members have been tiered; a family with no
  tiered member contributes no signal (treated as the simplest tier, since most of an
  estate is untiered until a Programme Manager acts on it, and treating "unknown" as
  "complex" would wrongly punish every family nobody has looked at yet).

Families are ordered by ``(-readiness, -usage, tier_score)`` — most ready, most used,
simplest first — and packed family-atomically: **a family is never split across trains**,
because §3.3's whole reason for a train to exist is that each family is designed once
within it. A family that alone exceeds a train's target size still lands entirely in that
train (a train is never left empty for want of a family that fits); families left over once
every configured train is full all land in the *last* train, because every Workbook must
end up ``IN_TRAIN`` somewhere — none are silently dropped.

**A train's gate schedule is a planned window, not a projection.** §13.1 gates a family at
G2 and a Migration Unit at G3; a train has no gate of its own. This module stores the
simplest honest roll-up: G2 clustered near the train's planned start, G3 near its planned
end — a first-cut plan a Programme Manager edits, not the throughput-based forecast §14.2's
wave scheduler produces (backlog S3.2.3, not built).

**No Wave node.** §3.3 also defines Wave as a calendar window containing one or more
trains; nothing in this story's acceptance criteria mentions one, so none is written here.
``Wave`` has been declared in the ontology since S1.1.1, unused, waiting for the story that
actually needs it.

**A re-run replaces every train it wrote — except one a Programme Manager has since
edited.** ``train_overrides.py`` (story S3.2.2) marks a train ``overridden`` the moment its
membership, sequence or WIP limits are touched from the Wave Board, mirroring S3.1.2's
``ModelFamily`` pinning exactly: ``run()`` reads that flag, leaves an overridden train (and
its members' families) alone, and excludes them from the free-packing pool for everyone
else — naming a train's id in ``confirm_train_ids`` lifts the pin for one run, the same as
``confirm_family_ids`` does for the Cartographer. Every fresh ``IN_TRAIN`` edge this module
writes starts at ``DEFAULT_MU_STATE`` — the Wave Board's kanban column a card first appears
in — and retiring every stale train's edges before writing new ones is what keeps "an MU is
IN_TRAIN exactly one train at a time" true across repeated proposals.
"""

from __future__ import annotations

import logging
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import asyncpg

from .cartographer import list_families
from .errors import InvalidRequestError
from .graph.queries import EDGE_INDEX_TABLE, ELEMENTS_OF_LABEL_SQL, NODE_INDEX_TABLE
from .ids import new_ulid
from .lineage import hydrate
from .migration_units import MU_STATES
from .principal import Principal
from .scope import ScopeStore
from .writes import EdgeWrite, GraphWriter, NodeWrite

logger = logging.getLogger(__name__)

#: Backlog S3.2.1's own worked example. No derivation for these five figures is given
#: anywhere in the spec (ADR 0025) — they are reproduced verbatim as the story's default.
BLACKROCK_DEFAULT_TRAIN_SIZES: tuple[int, ...] = (277, 328, 184, 177, 101)

#: Calendar days a train's planned window spans, chained back-to-back from the proposal's
#: start date. No spec/backlog figure exists for this; an editable default, not a rule.
DEFAULT_TRAIN_DURATION_DAYS = 30

_RETIREMENT_REASON = "superseded by a newer train proposal (story S3.2.1)"

#: migration_units.MU_STATES[1] — entered by "the Cartographer" per spec §3.2, and being
#: freshly assigned to a release train (this module's own job) is exactly that. The Wave
#: Board (S3.2.2, train_overrides.py) carries this value forward on a move or resequence
#: but never changes it — state transitions belong to whatever eventually builds §3.2's
#: state machine, not to this module or that one.
DEFAULT_MU_STATE = MU_STATES[1]

#: A safety bound on how many ReleaseTrain nodes a single read lists — no real programme
#: runs into this; it exists so a read has a bound at all.
_MAX_TRAINS = 10_000

#: SIMPLE < MODERATE < COMPLEX < REDESIGN, S1.4.1's own tier ladder.
_TIER_COMPLEXITY = {"SIMPLE": 0, "MODERATE": 1, "COMPLEX": 2, "REDESIGN": 3}

#: Position in a family's §12.2 lifecycle that "shared model readiness" ranks by — later is
#: more ready. Unrecognised/DEPRECATED states rank last rather than raising: nothing in
#: this codebase sets DEPRECATED yet, and a train proposal should degrade gracefully rather
#: than fail on a family in a state this module has never seen exercised.
_READINESS_RANK = {
    "SINGLETON": 0,
    "PROPOSED": 0,
    "DRAFT": 1,
    "IN_REVIEW": 2,
    "APPROVED": 3,
    "BUILT": 4,
    "PUBLISHED": 5,
}


@dataclass(frozen=True, slots=True)
class FamilySignal:
    """One family's ordering evidence — enough to sort by, and enough to explain a train."""

    id: str
    name: str
    state: str
    members: tuple[str, ...]
    usage_total: int
    tier_score: float | None
    """Mean tier complexity across tiered members; ``None`` if none has been tiered."""

    @property
    def readiness_rank(self) -> int:
        return _READINESS_RANK.get(self.state, -1)

    @property
    def sort_key(self) -> tuple[int, int, float]:
        return (-self.readiness_rank, -self.usage_total, self.tier_score or 0.0)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "state": self.state,
            "size": len(self.members),
            "usage_total": self.usage_total,
            "tier_score": self.tier_score,
        }


@dataclass(frozen=True, slots=True)
class TrainProposal:
    id: str
    name: str
    sequence: int
    """1-based position among the trains this run produced."""
    families: tuple[FamilySignal, ...]
    members: tuple[str, ...]
    """Every workbook id in the train, in write order (family order, then by usage)."""
    planned_start: str
    planned_end: str
    gate_schedule: dict[str, Any]
    explanation: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "sequence": self.sequence,
            "size": len(self.members),
            "family_count": len(self.families),
            "families": [f.as_dict() for f in self.families],
            "members": list(self.members),
            "planned_start": self.planned_start,
            "planned_end": self.planned_end,
            "gate_schedule": self.gate_schedule,
            "explanation": self.explanation,
        }


@dataclass(frozen=True, slots=True)
class TrainProposalResult:
    trains: tuple[TrainProposal, ...]
    train_sizes: tuple[int, ...]
    """The configured target sizes this run packed against."""
    family_count: int
    workbook_count: int
    unclustered_workbook_ids: tuple[str, ...]
    """Live workbooks with no live family — excluded from every train, since a train is
    ordered and packed by family and an unclustered workbook has none. Named explicitly
    rather than only counted, the same "don't hide a gap behind a number" choice the Estate
    Explorer's ``PENDING_COLUMNS`` made — a Programme Manager reading a proposal needs to
    know *which* workbooks it does not cover, not only how many."""
    elapsed_ms: float

    @property
    def unclustered_workbook_count(self) -> int:
        return len(self.unclustered_workbook_ids)

    def as_dict(self) -> dict[str, Any]:
        return {
            "trains": [t.as_dict() for t in self.trains],
            "configured_train_count": len(self.train_sizes),
            "trains_produced": len(self.trains),
            "train_sizes": list(self.train_sizes),
            "family_count": self.family_count,
            "workbook_count": self.workbook_count,
            "unclustered_workbook_ids": list(self.unclustered_workbook_ids),
            "unclustered_workbook_count": self.unclustered_workbook_count,
            "elapsed_ms": self.elapsed_ms,
        }


def validate_train_sizes(train_sizes: Sequence[int]) -> tuple[int, ...]:
    sizes = tuple(train_sizes)
    if not sizes:
        raise InvalidRequestError("train_sizes must name at least one train")
    if any(size <= 0 for size in sizes):
        raise InvalidRequestError("every train size must be a positive number of MUs")
    return sizes


def pack_trains(
    signals: Sequence[FamilySignal], train_sizes: Sequence[int]
) -> list[list[FamilySignal]]:
    """Family-atomic bin packing, in the ordering ``FamilySignal.sort_key`` already gives.

    A train is never left empty for want of a family that fits — the first candidate for a
    train is always accepted, even if it alone exceeds the target, because a family cannot
    be split. Families left over once every configured train has been considered land in
    the last one: every member must end up in a train, none are silently dropped.
    """
    ordered = sorted(signals, key=lambda s: s.sort_key)
    trains: list[list[FamilySignal]] = [[] for _ in train_sizes]
    sizes_used = [0] * len(train_sizes)

    for slot, target in enumerate(train_sizes):
        while ordered:
            candidate = ordered[0]
            if sizes_used[slot] == 0 or sizes_used[slot] + len(candidate.members) <= target:
                trains[slot].append(ordered.pop(0))
                sizes_used[slot] += len(candidate.members)
            else:
                break

    if ordered:
        trains[-1].extend(ordered)
    return trains


def train_window(sequence: int, start_date: date, duration_days: int) -> tuple[date, date]:
    train_start = start_date + timedelta(days=(sequence - 1) * duration_days)
    train_end = train_start + timedelta(days=duration_days - 1)
    return train_start, train_end


def gate_schedule(planned_start: date, planned_end: date) -> dict[str, Any]:
    return {
        "G2": {
            "planned_date": planned_start.isoformat(),
            "note": "family confirmation, clustered near the train's start",
        },
        "G3": {
            "planned_date": planned_end.isoformat(),
            "note": "MU acceptance, clustered near the train's end",
        },
    }


def explain_train(
    sequence: int,
    families: Sequence[FamilySignal],
    members: Sequence[str],
    tier_of: dict[str, str | None],
) -> str:
    leaders = ", ".join(
        f"'{f.name}' ({f.state}, {f.usage_total:,} views/90d)" for f in families[:3]
    )
    more = "" if len(families) <= 3 else f", and {len(families) - 3} more"
    tiers = Counter(tier_of.get(member) or "untiered" for member in members)
    tier_text = ", ".join(f"{count} {tier.lower()}" for tier, count in sorted(tiers.items()))
    return (
        f"Train {sequence} packs {len(members)} MUs across {len(families)} "
        f"famil{'y' if len(families) == 1 else 'ies'}. Families are ordered by shared-model "
        f"readiness, usage and tier mix; leading this train {'is' if len(families) == 1 else 'are'} "
        f"{leaders}{more}. Tier mix: {tier_text}."
    )


def order_members(
    families: Sequence[FamilySignal], usage_of: dict[str, int]
) -> tuple[str, ...]:
    ordered: list[str] = []
    for family in families:
        ordered.extend(
            sorted(family.members, key=lambda m: (-usage_of.get(m, 0), m))
        )
    return tuple(ordered)


async def list_trains(pool: asyncpg.Pool, graph_name: str) -> list[dict[str, Any]]:
    """Every ReleaseTrain, with its members in sequence — reads what ``run`` already wrote;
    computes nothing."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(ELEMENTS_OF_LABEL_SQL, graph_name, "ReleaseTrain", _MAX_TRAINS)
        train_ids = [row["id"] for row in rows]
        properties = await hydrate(conn, graph_name, "ReleaseTrain", train_ids)
        members = await _train_members(conn, graph_name, train_ids)
        names = await _workbook_names(conn, graph_name, members)

    trains = [
        _train_summary(train_id, props, members.get(train_id, []), names)
        for train_id, props in properties.items()
    ]
    return sorted(trains, key=lambda t: (t["name"], t["id"]))


async def get_train(pool: asyncpg.Pool, graph_name: str, train_id: str) -> dict[str, Any] | None:
    async with pool.acquire() as conn:
        properties = await hydrate(conn, graph_name, "ReleaseTrain", [train_id])
        if train_id not in properties:
            return None
        members = await _train_members(conn, graph_name, [train_id])
        names = await _workbook_names(conn, graph_name, members)
    return _train_summary(train_id, properties[train_id], members.get(train_id, []), names)


async def _train_members(
    conn: asyncpg.Connection, graph_name: str, train_ids: Sequence[str]
) -> dict[str, list[dict[str, Any]]]:
    """Each train's members, ordered by the ``IN_TRAIN`` edge's own ``sequence`` property —
    ``{"id", "sequence", "state"}`` per member, enough for the Wave Board's kanban card
    without a second round trip per card.

    ``estate_edge_index`` is an adjacency index (id/label/from_id/to_id) — it carries no
    properties, so ``sequence``/``state`` are read from the edge itself via ``hydrate``,
    the same "hydrate works for an edge label too" trick
    ``AgeGraphRepository.get_edge_record`` uses (S3.1.2): AGE stores an edge label's own
    table in the same id/properties shape a node label's table has.
    """
    if not train_ids:
        return {}
    rows = await conn.fetch(
        f"""
        SELECT e.id AS edge_id, e.from_id AS workbook, e.to_id AS train
        FROM {EDGE_INDEX_TABLE} e
        JOIN {NODE_INDEX_TABLE} n ON n.id = e.from_id AND n.kind = 'node'
         AND n.graph = $1 AND n.label = 'Workbook' AND n.retired_at IS NULL
        WHERE e.graph = $1 AND e.label = 'IN_TRAIN' AND e.to_id = ANY($2::text[])
          AND e.retired_at IS NULL
        """,
        graph_name,
        list(train_ids),
    )
    edge_properties = await hydrate(conn, graph_name, "IN_TRAIN", [row["edge_id"] for row in rows])

    ordered: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        props = edge_properties.get(row["edge_id"], {})
        ordered.setdefault(row["train"], []).append(
            {
                "id": row["workbook"],
                "sequence": int(props.get("sequence") or 0),
                "state": str(props.get("state") or DEFAULT_MU_STATE),
            }
        )
    return {
        train_id: sorted(members, key=lambda m: m["sequence"])
        for train_id, members in ordered.items()
    }


async def _workbook_names(
    conn: asyncpg.Connection, graph_name: str, members: Mapping[str, Sequence[dict[str, Any]]]
) -> dict[str, str]:
    ids = [member["id"] for train_members in members.values() for member in train_members]
    properties = await hydrate(conn, graph_name, "Workbook", ids)
    return {
        workbook_id: str(props.get("name") or workbook_id) for workbook_id, props in properties.items()
    }


def _train_summary(
    train_id: str,
    properties: dict[str, Any],
    members: list[dict[str, Any]],
    names: Mapping[str, str],
) -> dict[str, Any]:
    return {
        "id": train_id,
        "name": properties.get("name"),
        "size": len(members),
        "members": [
            {"id": m["id"], "name": names.get(m["id"], m["id"]), "sequence": m["sequence"], "state": m["state"]}
            for m in members
        ],
        "planned_start": properties.get("planned_start"),
        "planned_end": properties.get("planned_end"),
        "actual_start": properties.get("actual_start"),
        "actual_end": properties.get("actual_end"),
        "gate_schedule": properties.get("gate_schedule"),
        "wip_limits": properties.get("wip_limits"),
        "overridden": bool(properties.get("overridden")),
        "override_action": properties.get("override_action"),
        "override_reason": properties.get("override_reason"),
    }


async def train_event_subjects(pool: asyncpg.Pool, graph_name: str, train_id: str) -> list[str]:
    """Every subject id an event about this train could carry (story S3.2.2's "appears on
    the Programme timeline"): the train's own node id, plus every ``IN_TRAIN`` edge that
    has ever pointed at it — live or retired, since a retired one's own ``EDGE_RETIRED``
    event is exactly "this MU left this train."

    An event's ``subject`` is the one element a mutation touched (``events.py``), never a
    train id for an edge event — so reading "everything that happened to this train" means
    knowing every edge id first, then asking the outbox about each. ``GET /v1/events`` (see
    ``routes.py``) already answers "what happened to one subject"; this is what makes a
    train a subject-*set* a caller can actually resolve without knowing edge ids upfront.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT id FROM {EDGE_INDEX_TABLE}
             WHERE graph = $1 AND label = 'IN_TRAIN' AND to_id = $2
            """,
            graph_name,
            train_id,
        )
    return [train_id, *(row["id"] for row in rows)]


class TrainPlanner:
    """Proposes release trains from the estate's families and usage (§3.3, story S3.2.1)."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        graph_name: str,
        writer: GraphWriter,
        scope_store: ScopeStore,
    ) -> None:
        self._pool = pool
        self._graph = graph_name
        self._writer = writer
        self._scope = scope_store

    @property
    def pool(self) -> asyncpg.Pool:
        return self._pool

    @property
    def graph_name(self) -> str:
        return self._graph

    @property
    def writer(self) -> GraphWriter:
        """For ``train_overrides.py`` callers (move/resequence/WIP, S3.2.2) — they write
        through the same ``GraphWriter`` this engine does, so a train they touch and one
        this engine retires on the next run share one event source and one audit trail."""
        return self._writer

    async def _gather(
        self, *, excluded: frozenset[str] = frozenset()
    ) -> tuple[list[FamilySignal], dict[str, int], dict[str, str | None], tuple[str, ...]]:
        all_families = await list_families(self._pool, self._graph)
        # A family is pinned whole or not at all: train_overrides.py's own move_mu refuses
        # any move that would leave a family split across trains, so a family with one
        # excluded member has every member excluded — never counted as "unclustered" below,
        # since it is clustered, just not part of this run's free-packing pool.
        families = (
            [f for f in all_families if not any(m in excluded for m in f["members"])]
            if excluded
            else all_families
        )
        all_members = [member for family in families for member in family["members"]]
        scope_states = await self._scope.states()

        async with self._pool.acquire() as conn:
            workbook_props = await hydrate(conn, self._graph, "Workbook", all_members)
            all_workbooks = await conn.fetch(
                f"""
                SELECT id FROM {NODE_INDEX_TABLE}
                 WHERE graph = $1 AND kind = 'node' AND label = 'Workbook' AND retired_at IS NULL
                """,
                self._graph,
            )

        usage_of = {
            member_id: int(props.get("views_90d") or 0)
            for member_id, props in workbook_props.items()
        }
        tier_of = {
            member_id: scope_states[member_id].tier
            for member_id in all_members
            if member_id in scope_states
        }

        signals = []
        for family in families:
            members = tuple(family["members"])
            usage_total = sum(usage_of.get(m, 0) for m in members)
            tier_scores = [
                _TIER_COMPLEXITY[tier]
                for m in members
                if (tier := tier_of.get(m)) is not None and tier in _TIER_COMPLEXITY
            ]
            tier_score = (sum(tier_scores) / len(tier_scores)) if tier_scores else None
            signals.append(
                FamilySignal(
                    id=family["id"],
                    name=str(family["name"] or family["id"]),
                    state=str(family["state"]),
                    members=members,
                    usage_total=usage_total,
                    tier_score=tier_score,
                )
            )

        clustered = {m for family in all_families for m in family["members"]}
        unclustered = tuple(row["id"] for row in all_workbooks if row["id"] not in clustered)
        return signals, usage_of, tier_of, unclustered

    async def compute(
        self,
        *,
        train_sizes: Sequence[int] = BLACKROCK_DEFAULT_TRAIN_SIZES,
        start_date: date | None = None,
        duration_days: int = DEFAULT_TRAIN_DURATION_DAYS,
    ) -> TrainProposalResult:
        """Read the estate and propose trains. Writes nothing — see ``run`` for that."""
        return await self._propose(
            train_sizes=train_sizes, start_date=start_date, duration_days=duration_days
        )

    async def _propose(
        self,
        *,
        train_sizes: Sequence[int],
        start_date: date | None,
        duration_days: int,
        excluded: frozenset[str] = frozenset(),
    ) -> TrainProposalResult:
        started = time.perf_counter()
        sizes = validate_train_sizes(train_sizes)
        if duration_days <= 0:
            raise InvalidRequestError("duration_days must be positive")
        start = start_date or date.today()

        signals, usage_of, tier_of, unclustered = await self._gather(excluded=excluded)
        packed = pack_trains(signals, sizes)

        trains: list[TrainProposal] = []
        for families in packed:
            if not families:
                continue
            sequence = len(trains) + 1
            members = order_members(families, usage_of)
            planned_start, planned_end = train_window(sequence, start, duration_days)
            trains.append(
                TrainProposal(
                    id=new_ulid(),
                    name=f"Train {sequence}",
                    sequence=sequence,
                    families=tuple(families),
                    members=members,
                    planned_start=planned_start.isoformat(),
                    planned_end=planned_end.isoformat(),
                    gate_schedule=gate_schedule(planned_start, planned_end),
                    explanation=explain_train(sequence, families, members, tier_of),
                )
            )

        elapsed_ms = (time.perf_counter() - started) * 1000
        return TrainProposalResult(
            trains=tuple(trains),
            train_sizes=sizes,
            family_count=len(signals),
            workbook_count=sum(len(s.members) for s in signals) + len(unclustered),
            unclustered_workbook_ids=unclustered,
            elapsed_ms=round(elapsed_ms, 2),
        )

    async def run(
        self,
        *,
        principal: Principal,
        train_sizes: Sequence[int] = BLACKROCK_DEFAULT_TRAIN_SIZES,
        start_date: date | None = None,
        duration_days: int = DEFAULT_TRAIN_DURATION_DAYS,
        confirm_train_ids: frozenset[str] | None = None,
    ) -> TrainProposalResult:
        """Compute, then write: retire every train (and IN_TRAIN edge) this module owns
        and write the fresh proposal in its place — except a train a Programme Manager has
        since edited on the Wave Board (``overridden``, story S3.2.2), which is left alone
        and excluded from the free-packing pool, the same way ``Cartographer.run`` treats
        an overridden ``ModelFamily``. Naming a train's id in ``confirm_train_ids`` lifts
        that protection for this one run.
        """
        confirm_train_ids = confirm_train_ids or frozenset()

        async with self._pool.acquire() as conn:
            stale_trains = await conn.fetch(
                f"""
                SELECT id FROM {NODE_INDEX_TABLE}
                 WHERE graph = $1 AND kind = 'node' AND label = 'ReleaseTrain'
                   AND retired_at IS NULL
                """,
                self._graph,
            )
            stale_ids = [row["id"] for row in stale_trains]
            stale_properties = await hydrate(conn, self._graph, "ReleaseTrain", stale_ids)

        overridden_ids = frozenset(
            train_id
            for train_id, properties in stale_properties.items()
            if properties.get("overridden") and train_id not in confirm_train_ids
        )
        pinned_members: dict[str, list[dict[str, Any]]] = {}
        if overridden_ids:
            async with self._pool.acquire() as conn:
                pinned_members = await _train_members(conn, self._graph, list(overridden_ids))
        excluded = frozenset(
            member["id"] for members in pinned_members.values() for member in members
        )

        result = await self._propose(
            train_sizes=train_sizes,
            start_date=start_date,
            duration_days=duration_days,
            excluded=excluded,
        )

        async with self._pool.acquire() as conn:
            stale_edges = await conn.fetch(
                f"""
                SELECT id, to_id FROM {EDGE_INDEX_TABLE}
                 WHERE graph = $1 AND label = 'IN_TRAIN' AND retired_at IS NULL
                """,
                self._graph,
            )

        for row in stale_edges:
            if row["to_id"] in overridden_ids:
                continue
            await self._writer.retire_edge(
                row["id"], reason=_RETIREMENT_REASON, principal=principal
            )
        for train_id in stale_ids:
            if train_id in overridden_ids:
                continue
            await self._writer.retire_node(
                train_id, reason=_RETIREMENT_REASON, principal=principal
            )

        for train in result.trains:
            await self._writer.write_nodes(
                [
                    NodeWrite(
                        type="ReleaseTrain",
                        id=train.id,
                        properties={
                            "name": train.name,
                            "planned_start": train.planned_start,
                            "planned_end": train.planned_end,
                            "gate_schedule": train.gate_schedule,
                        },
                    )
                ],
                principal=principal,
            )
            for sequence, member in enumerate(train.members, start=1):
                await self._writer.write_edge(
                    EdgeWrite(
                        type="IN_TRAIN",
                        id=new_ulid(),
                        from_id=member,
                        to_id=train.id,
                        properties={"sequence": sequence, "state": DEFAULT_MU_STATE},
                    ),
                    principal=principal,
                )

        return result


__all__ = [
    "BLACKROCK_DEFAULT_TRAIN_SIZES",
    "DEFAULT_MU_STATE",
    "DEFAULT_TRAIN_DURATION_DAYS",
    "FamilySignal",
    "TrainPlanner",
    "TrainProposal",
    "TrainProposalResult",
    "explain_train",
    "gate_schedule",
    "get_train",
    "list_trains",
    "order_members",
    "pack_trains",
    "train_event_subjects",
    "train_window",
    "validate_train_sizes",
]
