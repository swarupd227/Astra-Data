"""The Cartographer: clustering workbooks into candidate model families — story S3.1.1.

    "I want workbooks clustered into candidate model families by shared lineage, so that the
    ~150-model planning assumption becomes a measured number in Month 1."

§12.1 gives the algorithm: for every workbook, the set of source tables it reaches, the set
of fields it encodes, and the multiset of calculated-field AST shapes it defines; similarity
between two workbooks is ``0.5*J(tables) + 0.3*J(fields) + 0.2*shared_shapes/max_shapes``;
agglomerative clustering (average linkage) at a configured threshold produces families;
families under the minimum size are merged into the nearest family or held as SINGLETON.

**The threshold is 0.55, not the backlog's 0.35.** The backlog's own rule (its line 69) is
that a disagreement with the specification is corrected in the specification's favour, and
§12.1 states the default plainly. The correction is not cosmetic: at 0.55, a pair with zero
shared tables can score at most ``0.3 + 0.2 = 0.5`` on fields and shapes alone, below the
threshold — so "every workbook pair sharing at least one table" (the backlog's own scoping
for which pairs are even considered) is not an optimisation *of* the spec's formula, it is a
mathematical *consequence* of the spec's own default. See ADR 0022.

**The formula is not reimplemented here.** ``similarity()``, its weights and ``ast_shape()``
already exist — S1.4.2 built them for the Lineage View, which computes the same three inputs
read-only so a model engineer can challenge a family's grouping. Reusing them is what makes
"the evidence that produced a family" and "the evidence the Lineage View shows for it" the
same numbers rather than two figures that happen to agree today.

**"Fields it encodes" is read from the Worksheet shelf properties, not ENCODES edges.**
§4.1.2 declares ``ENCODES`` (Worksheet→Field/CalculatedField, with a ``shelf`` property) and
the Tableau adapter does not yet emit it — ``Worksheet.rows_shelf``/``cols_shelf``/
``marks_shelf`` are populated (S2.3.2) but never materialised as graph edges. Flagged as a
follow-up rather than fixed here: fixing it means restructuring how the adapter's per-
datasource field maps are aggregated, which is F2.3's surface, not F3.1's. The shelf
properties carry exactly the field *names* a sheet places, which is what "encodes" needs, so
reading them directly is a faithful stand-in rather than a guess — see ADR 0022.

**"Calculated-field AST shapes it defines" does not have the same gap.** §12.1 says
*defines*, not *encodes*: a calculation belongs to a workbook because its datasource
``HAS_FIELD``-s it, whether or not any sheet ever places it on a shelf. That edge exists and
is written today, so calc shapes are read structurally and need no workaround.
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from itertools import combinations
from typing import Any

import asyncpg

from .graph.queries import EDGE_INDEX_TABLE, ELEMENTS_OF_LABEL_SQL, NODE_INDEX_TABLE
from .ids import new_ulid
from .lineage import calc_shapes, children, hydrate, reach, similarity
from .principal import Principal
from .retention import Programme, ProgrammeStore
from .writes import EdgeWrite, GraphWriter, NodeWrite

logger = logging.getLogger(__name__)

#: §12.1's default. See this module's docstring for why it is 0.55 and not the backlog's 0.35.
DEFAULT_THRESHOLD = 0.55

#: §12.1's default minimum family size.
DEFAULT_MIN_FAMILY_SIZE = 3

#: A safety bound, not a design limit — no real estate reaches this many workbooks.
MAX_ESTATE_WORKBOOKS = 200_000

#: The states a ModelFamily is in *only* because the Cartographer put it there, and
#: therefore the states a re-run may retire and replace outright. Anything else (DRAFT and
#: beyond) is a human decision (S3.1.2) and this module never touches it.
CARTOGRAPHER_OWNED_STATES = frozenset({"PROPOSED", "SINGLETON"})

_MIN_RETIREMENT_REASON = "superseded by a newer Cartographer clustering run (story S3.1.1)"


# ------------------------------------------------------------------------- pure algorithm


def agglomerative_clusters(
    workbook_ids: Iterable[str],
    pair_strength: Mapping[tuple[str, str], float],
    *,
    threshold: float,
) -> list[frozenset[str]]:
    """Average-linkage agglomerative clustering, stopped at ``threshold`` (§12.1).

    Every workbook starts as its own cluster. At each step the pair of clusters with the
    highest average pairwise similarity is merged, using the Lance-Williams update — a
    merged cluster's similarity to every other cluster is the size-weighted average of its
    two parents' similarities to it — so a merge costs one pass over the *other* active
    clusters rather than a re-scan of every underlying workbook pair. A workbook that shares
    a table with nobody never enters the merge loop at all: nothing pulls it above a
    positive threshold, and it survives as its own cluster by construction.

    ``pair_strength`` keys are canonical ``(a, b)`` with ``a < b`` (see ``_pair``), matching
    every qualifying workbook pair — S3.1.1 restricts this to pairs sharing at least one
    table (see the module docstring for why that is exact at the spec's own threshold, not
    an approximation of it).
    """
    clusters: set[frozenset[str]] = {frozenset({workbook}) for workbook in workbook_ids}
    if len(clusters) <= 1:
        return list(clusters)

    link: dict[frozenset[frozenset[str]], float] = {}
    for (left, right), score in pair_strength.items():
        if score <= 0:
            continue
        left_cluster, right_cluster = frozenset({left}), frozenset({right})
        if left_cluster in clusters and right_cluster in clusters:
            link[frozenset({left_cluster, right_cluster})] = score

    while link:
        best_key, best_score = max(link.items(), key=lambda item: (item[1], _tie_break(item[0])))
        if best_score < threshold:
            break

        left_cluster, right_cluster = sorted(best_key, key=lambda cluster: sorted(cluster)[0])
        merged = left_cluster | right_cluster
        left_size, right_size = len(left_cluster), len(right_cluster)

        stale = [key for key in link if left_cluster in key or right_cluster in key]
        updated: dict[frozenset[str], float] = {}
        for key in stale:
            other = next(iter(key - {left_cluster, right_cluster}), None)
            if other is None:  # the left/right pair itself
                continue
            score_left = link.get(frozenset({left_cluster, other}), 0.0)
            score_right = link.get(frozenset({right_cluster, other}), 0.0)
            new_score = (left_size * score_left + right_size * score_right) / (
                left_size + right_size
            )
            if new_score > 0:
                updated[other] = new_score

        for key in stale:
            del link[key]
        for other, score in updated.items():
            link[frozenset({merged, other})] = score

        clusters.discard(left_cluster)
        clusters.discard(right_cluster)
        clusters.add(merged)

    return list(clusters)


def _tie_break(pair: frozenset[frozenset[str]]) -> tuple[str, ...]:
    """Deterministic ordering for clusters tied on similarity, so a re-run of the same
    graph produces the same merges — content-derived, never dict iteration order."""
    return tuple(sorted(sorted(cluster)[0] for cluster in pair))


@dataclass(frozen=True, slots=True)
class Resolution:
    """The result of resolving families under the minimum size (§12.1's second sentence)."""

    families: list[frozenset[str]]
    singletons: dict[frozenset[str], str]
    """Cluster to the reason it was held rather than merged."""


def resolve_undersized(
    clusters: Sequence[frozenset[str]],
    pair_strength: Mapping[tuple[str, str], float],
    *,
    min_family_size: int,
) -> Resolution:
    """Merge a too-small cluster into its nearest neighbour, or hold it as SINGLETON.

    "Nearest" is the *raw* average similarity to every workbook in the other cluster —
    recomputed here from ``pair_strength`` rather than continuing the Lance-Williams state
    from ``agglomerative_clusters``, because this pass considers merges the main threshold
    already rejected, and there are always few enough undersized clusters that recomputing
    from the workbook pairs is simpler to get right than resuming an approximation.

    A merge that leaves a cluster still under the minimum is not accepted as good enough —
    it is still "a family under a minimum size" by the same rule, and is reconsidered on
    the next pass through the loop like any other. SINGLETON is what a cluster gets when
    that process runs out of anyone left to merge into with a positive average: usually
    because it (or everything reachable from it) shares no table with the rest of the
    estate at all — S3.1.1 only ever scores pairs that do (see the module docstring) — but
    it can also be two or three workbooks whose only lineage was to each other, held
    together and still short of the floor. Either way the label means the same thing to a
    reviewer: nothing in this run could responsibly make it bigger.
    """
    active: dict[frozenset[str], None] = dict.fromkeys(clusters)
    reasons: dict[frozenset[str], str] = {}

    while True:
        undersized = [
            cluster
            for cluster in active
            if len(cluster) < min_family_size and cluster not in reasons
        ]
        if not undersized:
            break
        target = min(undersized, key=lambda cluster: (len(cluster), sorted(cluster)[0]))

        best: frozenset[str] | None = None
        best_score = 0.0
        for other in active:
            if other == target or other in reasons:
                continue
            score = _average_similarity(target, other, pair_strength)
            if score > best_score or (
                score == best_score
                and best is not None
                and sorted(other)[0] < sorted(best)[0]
            ):
                best, best_score = other, score

        if best is None or best_score <= 0.0:
            reasons[target] = (
                f"below the minimum family size ({min_family_size}) and shares no lineage "
                f"with any other candidate family"
            )
            continue

        del active[target]
        del active[best]
        active[target | best] = None

    resolved = [cluster for cluster in active if cluster not in reasons]
    return Resolution(families=resolved, singletons=dict(reasons))


def _average_similarity(
    left: frozenset[str], right: frozenset[str], pair_strength: Mapping[tuple[str, str], float]
) -> float:
    scores = [pair_strength.get(_pair(a, b), 0.0) for a in left for b in right]
    return sum(scores) / len(scores) if scores else 0.0


def _pair(left: str, right: str) -> tuple[str, str]:
    return (left, right) if left <= right else (right, left)


def candidate_grain(sheet_dimension_sets: Iterable[frozenset[str]]) -> tuple[str, ...]:
    """§12.1: "the most frequent minimal dimension set across member sheets".

    Both adjectives, in one tie-break: highest count first, then the smaller set (more
    literally minimal), then lexicographic — so a re-run over an unchanged estate always
    names the same grain.
    """
    counted = Counter(s for s in sheet_dimension_sets if s)
    if not counted:
        return ()
    winner = min(counted.items(), key=lambda item: (-item[1], len(item[0]), sorted(item[0])))
    return tuple(sorted(winner[0]))


@dataclass(frozen=True, slots=True)
class FamilyEvidence:
    """§12.1's evidence: what member workbooks actually share, not what any one reaches."""

    shared_tables: tuple[str, ...]
    shared_fields: tuple[str, ...]
    shared_calc_shapes: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "shared_tables": list(self.shared_tables),
            "shared_fields": list(self.shared_fields),
            "shared_calc_shapes": self.shared_calc_shapes,
        }


def family_evidence(
    members: Iterable[str], tables_of: Mapping[str, set[str]], fields_of: Mapping[str, set[str]],
    calc_shapes_of: Mapping[str, set[str]],
) -> FamilyEvidence:
    """A table, field or shape counts as evidence when *two or more* members reach it —
    the fact the clustering was actually made from, not the union of everything any one
    member happens to touch."""
    members = list(members)
    return FamilyEvidence(
        shared_tables=tuple(sorted(_shared_by_two_or_more(members, tables_of))),
        shared_fields=tuple(sorted(_shared_by_two_or_more(members, fields_of))),
        shared_calc_shapes=len(_shared_by_two_or_more(members, calc_shapes_of)),
    )


def _shared_by_two_or_more(members: Sequence[str], reach_of: Mapping[str, set[str]]) -> set[str]:
    counts: Counter[str] = Counter()
    for member in members:
        counts.update(reach_of.get(member, set()))
    return {item for item, count in counts.items() if count >= 2}


# --------------------------------------------------------------------------- orchestration


@dataclass(frozen=True, slots=True)
class FamilyProposal:
    id: str
    name: str
    state: str
    members: tuple[str, ...]
    grain: tuple[str, ...]
    evidence: FamilyEvidence
    reason: str | None = None
    overridden: bool = False
    override_action: str | None = None
    override_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "state": self.state,
            "members": list(self.members),
            "size": len(self.members),
            "grain": list(self.grain),
            "reason": self.reason,
            "evidence": self.evidence.as_dict(),
            "overridden": self.overridden,
            "override_action": self.override_action,
            "override_reason": self.override_reason,
        }


@dataclass(frozen=True, slots=True)
class Gathered:
    """§12.1's three inputs, read for a set of workbooks — the whole estate for a normal
    run, or a handful for a split/merge/move (``family_overrides.py``) that only needs to
    recompute grain and evidence for the families an override touched.
    """

    workbook_ids: tuple[str, ...]
    workbook_names: dict[str, dict[str, Any]]
    sheets: dict[str, set[str]]
    sheet_dimensions: dict[str, frozenset[str]]
    tables_of: dict[str, set[str]]
    fields_of: dict[str, set[str]]
    shapes_of: dict[str, set[str]]


async def gather_reach(
    pool: asyncpg.Pool, graph_name: str, workbook_ids: Sequence[str]
) -> Gathered:
    """The whole chain, for exactly the workbooks asked for. §12.1's own reasoning, and
    S3.1.1's ADR (0022), apply here too: this reuses ``children``/``hydrate``/
    ``calc_shapes`` from ``lineage.py`` rather than a second implementation of the same
    hops, and reads ``rows_shelf``/``cols_shelf``/``marks_shelf`` rather than ``ENCODES``
    for the same reason S3.1.1 does (the adapter does not write it yet).
    """
    async with pool.acquire() as conn:
        workbook_names = await hydrate(conn, graph_name, "Workbook", workbook_ids)
        sheets = await children(conn, graph_name, workbook_ids, "CONTAINS", "Worksheet")
        sheet_ids = sorted({s for owned in sheets.values() for s in owned})
        sheet_properties = await hydrate(conn, graph_name, "Worksheet", sheet_ids)

        datasources = await children(
            conn, graph_name, sheet_ids, "USES_DATASOURCE", "Datasource"
        )
        datasource_ids = sorted({d for owned in datasources.values() for d in owned})
        connections = await children(
            conn, graph_name, datasource_ids, "CONNECTS_TO", "Connection"
        )
        connection_ids = sorted({c for owned in connections.values() for c in owned})
        tables = await children(conn, graph_name, connection_ids, "CONNECTS_TO", "Table")
        datasource_calcs = await children(
            conn, graph_name, datasource_ids, "HAS_FIELD", "CalculatedField"
        )
        calc_ids = sorted({c for owned in datasource_calcs.values() for c in owned})
        shapes = await calc_shapes(conn, graph_name, calc_ids) if calc_ids else {}

    tables_of, fields_of, shapes_of = _per_workbook_reach(
        workbook_ids, sheets, datasources, connections, tables, datasource_calcs, shapes,
        sheet_properties,
    )
    sheet_dimensions = {
        sheet_id: _sheet_dimensions(sheet_properties.get(sheet_id, {})) for sheet_id in sheet_ids
    }
    return Gathered(
        workbook_ids=tuple(workbook_ids),
        workbook_names=workbook_names,
        sheets=sheets,
        sheet_dimensions=sheet_dimensions,
        tables_of=tables_of,
        fields_of=fields_of,
        shapes_of=shapes_of,
    )


@dataclass(frozen=True, slots=True)
class ChangeIfConfirmed:
    """What a re-run would do to one currently-overridden family, if that family's
    protection were lifted by naming it in ``confirm_family_ids`` (S3.1.2's own words: "a
    re-run reports what it would change and does not change overridden families without
    confirmation")."""

    family_id: str
    current_members: tuple[str, ...]
    proposed_members: tuple[str, ...]

    @property
    def unchanged(self) -> bool:
        return set(self.current_members) == set(self.proposed_members)

    def as_dict(self) -> dict[str, Any]:
        return {
            "family_id": self.family_id,
            "current_members": list(self.current_members),
            "proposed_members": list(self.proposed_members),
            "unchanged": self.unchanged,
        }


@dataclass(frozen=True, slots=True)
class _Computation:
    """``compute``'s result, plus the pairwise scores ``run`` needs to write
    ``SHARES_LINEAGE`` and nobody else does. ``result.would_change`` already carries
    whatever an unconstrained run would have proposed for a pinned family."""

    result: ClusteringResult
    pair_scores: dict[tuple[str, str], tuple[float, float, float, int]]


@dataclass(frozen=True, slots=True)
class ClusteringResult:
    families: tuple[FamilyProposal, ...]
    """The families this run **applied** — fresh proposals only. A family a re-run left
    pinned (S3.1.2) because it is overridden and not confirmed is not in here; it already
    exists in the graph unchanged, and ``would_change`` says what this run would have done
    to it if asked to."""

    workbook_count: int
    pair_count: int
    threshold: float
    min_family_size: int
    elapsed_ms: float
    would_change: tuple[ChangeIfConfirmed, ...] = ()
    """Populated only by ``run`` when at least one family was pinned (§12.1: "a re-run
    reports what it would change"). Empty for ``compute``, which has nothing to compare
    against — it never reads or writes the graph's current families."""

    @property
    def family_count(self) -> int:
        return sum(1 for f in self.families if f.state == "PROPOSED")

    @property
    def singleton_count(self) -> int:
        return sum(1 for f in self.families if f.state == "SINGLETON")

    @property
    def histogram(self) -> dict[str, int]:
        """Member count → number of families with that many members."""
        counts: Counter[int] = Counter(len(f.members) for f in self.families)
        return {str(size): count for size, count in sorted(counts.items())}

    def as_dict(self) -> dict[str, Any]:
        return {
            "family_count": self.family_count,
            "singleton_count": self.singleton_count,
            "distribution": {
                "PROPOSED": self.family_count,
                "SINGLETON": self.singleton_count,
            },
            "histogram": self.histogram,
            "workbook_count": self.workbook_count,
            "pair_count": self.pair_count,
            "threshold": self.threshold,
            "min_family_size": self.min_family_size,
            "elapsed_ms": self.elapsed_ms,
            "families": [f.as_dict() for f in self.families],
            "would_change": [change.as_dict() for change in self.would_change],
        }


class Cartographer:
    """Reads the whole estate, clusters it, and writes the result (§12.1, S3.1.1)."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        graph_name: str,
        writer: GraphWriter,
        programme_store: ProgrammeStore | None = None,
    ) -> None:
        self._pool = pool
        self._graph = graph_name
        self._writer = writer
        self._programmes = programme_store

    @property
    def pool(self) -> asyncpg.Pool:
        """For the read-only ``list_families``/``get_family`` callers — the API routes
        need a pool and there is no reason to make them reach past this object for one."""
        return self._pool

    @property
    def graph_name(self) -> str:
        return self._graph

    @property
    def writer(self) -> GraphWriter:
        """For ``family_overrides.py`` callers (split/merge/move, S3.1.2) — they write
        through the same ``GraphWriter`` this engine does, so a family they touch and one
        this engine retires on the next run share one event source and one audit trail."""
        return self._writer

    async def compute(
        self,
        *,
        threshold: float = DEFAULT_THRESHOLD,
        min_family_size: int = DEFAULT_MIN_FAMILY_SIZE,
    ) -> ClusteringResult:
        """Read the estate and cluster it. Writes nothing — see ``run`` for that."""
        computation = await self._compute(threshold=threshold, min_family_size=min_family_size)
        return computation.result

    async def _compute(
        self,
        *,
        threshold: float,
        min_family_size: int,
        pinned: Mapping[str, tuple[str, ...]] | None = None,
    ) -> _Computation:
        """The same read-and-cluster ``compute`` does, plus what ``run`` needs and nobody
        else does — kept private so a caller who only wants the result (``compute``) is
        never handed the intermediate maps it has no use for.

        ``pinned`` is family id → its current members: workbooks a human has already put
        somewhere on purpose (S3.1.2). They are excluded from clustering entirely — never a
        candidate to merge into anything, never left to drift — but ``SHARES_LINEAGE`` is
        still written for every qualifying pair regardless: it is evidence about the estate,
        not a clustering decision, and pinning a family does not make two workbooks share
        fewer tables. When anything is pinned, an *unconstrained* clustering is also run,
        purely to report what each pinned family's members would have become — §12.1's own
        "reports what it would change" — without writing any of it.
        """
        started = time.perf_counter()
        pinned = pinned or {}
        pinned_ids = frozenset(w for members in pinned.values() for w in members)

        async with self._pool.acquire() as conn:
            workbook_rows = await conn.fetch(
                ELEMENTS_OF_LABEL_SQL, self._graph, "Workbook", MAX_ESTATE_WORKBOOKS
            )
            workbook_ids = [row["id"] for row in workbook_rows]

        gathered = await gather_reach(self._pool, self._graph, workbook_ids)
        pair_scores = _pair_similarity(
            gathered.workbook_ids, gathered.tables_of, gathered.fields_of, gathered.shapes_of
        )

        free_ids = [wid for wid in gathered.workbook_ids if wid not in pinned_ids]
        free_pairs = {
            pair: score[0]
            for pair, score in pair_scores.items()
            if pair[0] not in pinned_ids and pair[1] not in pinned_ids
        }
        resolved = _cluster(free_ids, free_pairs, threshold, min_family_size)
        proposals = _proposals_from(resolved, gathered)

        would_change: tuple[ChangeIfConfirmed, ...] = ()
        if pinned:
            all_pairs = {pair: score[0] for pair, score in pair_scores.items()}
            unconstrained = _cluster(
                list(gathered.workbook_ids), all_pairs, threshold, min_family_size
            )
            proposed_of: dict[str, frozenset[str]] = {}
            for members in unconstrained.families:
                for workbook_id in members:
                    proposed_of[workbook_id] = members
            for members, _reason in unconstrained.singletons.items():
                for workbook_id in members:
                    proposed_of[workbook_id] = members

            would_change = tuple(
                ChangeIfConfirmed(
                    family_id=family_id,
                    current_members=members,
                    proposed_members=tuple(
                        sorted(
                            {
                                workbook_id
                                for member in members
                                for workbook_id in proposed_of.get(
                                    member, frozenset({member})
                                )
                            }
                        )
                    ),
                )
                for family_id, members in sorted(pinned.items())
            )

        elapsed_ms = (time.perf_counter() - started) * 1000
        result = ClusteringResult(
            families=tuple(proposals),
            workbook_count=len(gathered.workbook_ids),
            pair_count=len(pair_scores),
            threshold=threshold,
            min_family_size=min_family_size,
            elapsed_ms=round(elapsed_ms, 2),
            would_change=would_change,
        )
        return _Computation(result=result, pair_scores=pair_scores)

    async def run(
        self,
        *,
        principal: Principal,
        threshold: float = DEFAULT_THRESHOLD,
        min_family_size: int = DEFAULT_MIN_FAMILY_SIZE,
        confirm_family_ids: frozenset[str] | None = None,
    ) -> ClusteringResult:
        """Compute, then write: retire the Cartographer's own prior proposals, write the
        fresh ones, upsert the pairwise evidence, and record the run on the open programme.

        Retiring is scoped to ``CARTOGRAPHER_OWNED_STATES``: a family a human has already
        accepted (DRAFT and beyond, S3.1.2's later stories) is never touched by a re-run,
        because nothing in this module has any business overwriting a decision it did not
        make. Within those states, a family a human has split, merged or moved a member
        into (``overridden``, S3.1.2) is *also* left alone — its members are excluded from
        clustering entirely, and ``result.would_change`` reports what an unconstrained run
        would have proposed for them instead. Naming a family's id in
        ``confirm_family_ids`` lifts that protection for this one run: its members re-enter
        clustering and the family may be retired and replaced like any other.
        """
        confirm_family_ids = confirm_family_ids or frozenset()

        async with self._pool.acquire() as conn:
            stale = await conn.fetch(
                f"""
                SELECT id FROM {NODE_INDEX_TABLE}
                WHERE graph = $1 AND label = 'ModelFamily' AND kind = 'node'
                  AND retired_at IS NULL
                """,
                self._graph,
            )
            stale_ids = [row["id"] for row in stale]
            stale_properties = await hydrate(conn, self._graph, "ModelFamily", stale_ids)
            overridden_ids = [
                family_id
                for family_id, properties in stale_properties.items()
                if properties.get("state") in CARTOGRAPHER_OWNED_STATES
                and properties.get("overridden")
                and family_id not in confirm_family_ids
            ]
            overridden_members = await _family_members(conn, self._graph, overridden_ids)

        pinned = {
            family_id: tuple(sorted(members))
            for family_id, members in overridden_members.items()
            if members
        }

        computation = await self._compute(
            threshold=threshold, min_family_size=min_family_size, pinned=pinned
        )
        result = computation.result

        for family_id, properties in stale_properties.items():
            if properties.get("state") not in CARTOGRAPHER_OWNED_STATES:
                continue
            if family_id in pinned:
                continue
            await self._writer.retire_node(
                family_id, reason=_MIN_RETIREMENT_REASON, principal=principal
            )

        for proposal in result.families:
            await self._writer.write_nodes(
                [
                    NodeWrite(
                        type="ModelFamily",
                        id=proposal.id,
                        properties=_family_properties(proposal),
                    )
                ],
                principal=principal,
            )
            for member in proposal.members:
                await self._writer.write_edge(
                    EdgeWrite(
                        type="IN_FAMILY",
                        id=new_ulid(),
                        from_id=member,
                        to_id=proposal.id,
                        properties={"confidence": 1.0},
                    ),
                    principal=principal,
                )

        for (left, right), (_strength, j_tables, j_fields, shared_shapes) in (
            computation.pair_scores.items()
        ):
            await self._writer.upsert_edge(
                EdgeWrite(
                    type="SHARES_LINEAGE",
                    id=_derived_edge_id("SHARES_LINEAGE", left, right),
                    from_id=left,
                    to_id=right,
                    properties={
                        "jaccard_tables": j_tables,
                        "jaccard_fields": j_fields,
                        "shared_calc_count": shared_shapes,
                    },
                ),
                principal=principal,
            )

        if self._programmes is not None:
            open_programme = await self._open_programme()
            if open_programme is not None:
                await self._programmes.record_clustering(
                    open_programme.id, stats=result.as_dict(), principal=principal.value
                )

        return result

    async def _open_programme(self) -> Programme | None:
        """The programme a run's figures are recorded against.

        Nothing stops two programmes being open at once, so "the" open programme is the
        most recently *started* one — the platform's working assumption is one active
        engagement at a time, and if that is ever wrong, silently updating the newest
        rather than an arbitrary one is the safer failure.
        """
        assert self._programmes is not None
        open_programmes = [p for p in await self._programmes.programmes() if p.open]
        if not open_programmes:
            return None
        return max(open_programmes, key=lambda p: p.started_at)


# ------------------------------------------------------------------------------- reading


async def list_families(
    pool: asyncpg.Pool, graph_name: str, *, state: str | None = None
) -> list[dict[str, Any]]:
    """Every ModelFamily, with its members — for the console and for a human checking a
    run's output. Reads what ``run`` already wrote; computes nothing."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            ELEMENTS_OF_LABEL_SQL, graph_name, "ModelFamily", MAX_ESTATE_WORKBOOKS
        )
        family_ids = [row["id"] for row in rows]
        properties = await hydrate(conn, graph_name, "ModelFamily", family_ids)
        members = await _family_members(conn, graph_name, family_ids)

    families = [
        _family_summary(family_id, props, members.get(family_id, []))
        for family_id, props in properties.items()
        if state is None or props.get("state") == state
    ]
    return sorted(families, key=lambda f: (f["name"], f["id"]))


async def count_families(pool: asyncpg.Pool, graph_name: str) -> int:
    """Every live ModelFamily, regardless of state (story S3.1.3).

    Deliberately every state, not just PROPOSED (``ClusteringResult.family_count`` counts
    only what one run applied): a SINGLETON is still one workbook's own family, and a
    family a human split, merged or moved (S3.1.2) is still a family — the "~150 shared
    governed models" a Programme Manager confirms is the estate's whole family count as it
    stands today, not one algorithm's opinion of it.
    """
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            f"""
            SELECT count(*) FROM {NODE_INDEX_TABLE}
             WHERE graph = $1 AND kind = 'node' AND label = 'ModelFamily' AND retired_at IS NULL
            """,
            graph_name,
        )
    return int(count)


async def get_family(pool: asyncpg.Pool, graph_name: str, family_id: str) -> dict[str, Any] | None:
    async with pool.acquire() as conn:
        properties = await hydrate(conn, graph_name, "ModelFamily", [family_id])
        if family_id not in properties:
            return None
        members = await _family_members(conn, graph_name, [family_id])
    return _family_summary(family_id, properties[family_id], members.get(family_id, []))


async def _family_members(
    conn: asyncpg.Connection, graph_name: str, family_ids: Sequence[str]
) -> dict[str, list[str]]:
    if not family_ids:
        return {}
    rows = await conn.fetch(
        f"""
        SELECT e.from_id AS workbook, e.to_id AS family
        FROM {EDGE_INDEX_TABLE} e
        JOIN {NODE_INDEX_TABLE} n ON n.id = e.from_id AND n.kind = 'node'
         AND n.graph = $1 AND n.label = 'Workbook' AND n.retired_at IS NULL
        WHERE e.graph = $1 AND e.label = 'IN_FAMILY' AND e.to_id = ANY($2::text[])
          AND e.retired_at IS NULL
        """,
        graph_name,
        list(family_ids),
    )
    out: dict[str, list[str]] = {}
    for row in rows:
        out.setdefault(row["family"], []).append(row["workbook"])
    return out


def _family_summary(
    family_id: str, properties: dict[str, Any], members: list[str]
) -> dict[str, Any]:
    grain = str(properties.get("grain") or "")
    return {
        "id": family_id,
        "name": properties.get("name"),
        "state": properties.get("state"),
        "domain": properties.get("domain"),
        "owner": properties.get("owner"),
        "grain": [part.strip() for part in grain.split(",") if part.strip()],
        "conformed_dims": list(properties.get("conformed_dims") or []),
        "reason": properties.get("reason"),
        "members": sorted(members),
        "size": len(members),
        "evidence": {
            "shared_tables": list(properties.get("evidence_shared_tables") or []),
            "shared_fields": list(properties.get("evidence_shared_fields") or []),
            "shared_calc_shapes": properties.get("evidence_shared_calc_shapes") or 0,
        },
        "overridden": bool(properties.get("overridden")),
        "override_action": properties.get("override_action"),
        "override_reason": properties.get("override_reason"),
        "conformance_ruleset_version": properties.get("conformance_ruleset_version"),
    }


# ------------------------------------------------------------------------------- helpers


def _per_workbook_reach(
    workbook_ids: Sequence[str],
    sheets: dict[str, set[str]],
    datasources: dict[str, set[str]],
    connections: dict[str, set[str]],
    tables: dict[str, set[str]],
    datasource_calcs: dict[str, set[str]],
    shapes: dict[str, str],
    sheet_properties: dict[str, dict[str, Any]],
) -> tuple[dict[str, set[str]], dict[str, set[str]], dict[str, set[str]]]:
    tables_of: dict[str, set[str]] = {}
    fields_of: dict[str, set[str]] = {}
    shapes_of: dict[str, set[str]] = {}

    for workbook_id in workbook_ids:
        rolled = reach(
            sheets.get(workbook_id, set()), datasources, connections, tables, {}, {}
        )
        tables_of[workbook_id] = rolled.tables

        fields: set[str] = set()
        for sheet_id in sheets.get(workbook_id, set()):
            fields |= _sheet_encoded_fields(sheet_properties.get(sheet_id, {}))
        fields_of[workbook_id] = fields

        workbook_shapes: set[str] = set()
        for datasource_id in rolled.datasources:
            for calc_id in datasource_calcs.get(datasource_id, set()):
                if calc_id in shapes:
                    workbook_shapes.add(shapes[calc_id])
        shapes_of[workbook_id] = workbook_shapes

    return tables_of, fields_of, shapes_of


def _sheet_encoded_fields(properties: dict[str, Any]) -> set[str]:
    """Every field name a sheet places, on any shelf — rows, cols or marks.

    Marks-shelf entries are written ``"attr:fieldname"`` (sheets.py's ``_encodings``); the
    channel is not the field's identity, so only the name after the colon is kept.
    """
    names: set[str] = set()
    for name in properties.get("rows_shelf") or ():
        if name:
            names.add(str(name))
    for name in properties.get("cols_shelf") or ():
        if name:
            names.add(str(name))
    for entry in properties.get("marks_shelf") or ():
        text = str(entry)
        names.add(text.split(":", 1)[1] if ":" in text else text)
    return names


def _sheet_dimensions(properties: dict[str, Any]) -> frozenset[str]:
    """A sheet's own candidate grain: what is on rows and cols, not marks.

    Marks (colour, size, detail...) are encoding channels, not grouping — §12.1's grain is
    "the most frequent minimal *dimension* set", and a sheet's row/column shelves are what
    Tableau itself calls the axes a view is grouped by.
    """
    names: set[str] = set()
    for name in properties.get("rows_shelf") or ():
        if name:
            names.add(str(name))
    for name in properties.get("cols_shelf") or ():
        if name:
            names.add(str(name))
    return frozenset(names)


def _pair_similarity(
    workbook_ids: Sequence[str],
    tables_of: Mapping[str, set[str]],
    fields_of: Mapping[str, set[str]],
    shapes_of: Mapping[str, set[str]],
) -> dict[tuple[str, str], tuple[float, float, float, int]]:
    """§12.1's similarity, for every workbook pair sharing at least one table.

    Restricting to table-sharing pairs is exact at the spec's own 0.55 default, not merely
    an optimisation of it — see the module docstring. Found through a table→workbooks
    inverted index rather than the full O(n²) pairs, the same technique S1.4.2 uses for the
    Lineage View and for the same reason: on a real estate almost every pair shares nothing.
    """
    by_table: dict[str, list[str]] = {}
    for workbook_id in workbook_ids:
        for table_id in tables_of.get(workbook_id, set()):
            by_table.setdefault(table_id, []).append(workbook_id)

    scores: dict[tuple[str, str], tuple[float, float, float, int]] = {}
    for holders in by_table.values():
        if len(holders) < 2:
            continue
        for left, right in combinations(sorted(holders), 2):
            pair = _pair(left, right)
            if pair in scores:
                continue
            strength, j_tables, j_fields, shared_shapes = similarity(
                (tables_of.get(left, set()), tables_of.get(right, set())),
                (fields_of.get(left, set()), fields_of.get(right, set())),
                (shapes_of.get(left, set()), shapes_of.get(right, set())),
            )
            scores[pair] = (strength, j_tables, j_fields, shared_shapes)
    return scores


def _family_name(members: tuple[str, ...], workbook_names: Mapping[str, dict[str, Any]]) -> str:
    named = sorted(str(workbook_names.get(m, {}).get("name", m)) for m in members)
    if len(named) == 1:
        return f"{named[0]} (singleton)"
    return f"{named[0]} + {len(named) - 1} more"


def _family_properties(proposal: FamilyProposal) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "name": proposal.name,
        "state": proposal.state,
        "grain": ", ".join(proposal.grain),
        "conformed_dims": [],
        "evidence_shared_tables": list(proposal.evidence.shared_tables),
        "evidence_shared_fields": list(proposal.evidence.shared_fields),
        "evidence_shared_calc_shapes": proposal.evidence.shared_calc_shapes,
    }
    if proposal.reason is not None:
        properties["reason"] = proposal.reason
    return properties


def _cluster(
    workbook_ids: Sequence[str],
    pair_strength: Mapping[tuple[str, str], float],
    threshold: float,
    min_family_size: int,
) -> Resolution:
    """The two pure-algorithm steps together — clustering, then the undersized pass — used
    for both the applied result and, when anything is pinned, the unconstrained "what would
    this have been" comparison."""
    clustered = agglomerative_clusters(workbook_ids, pair_strength, threshold=threshold)
    return resolve_undersized(clustered, pair_strength, min_family_size=min_family_size)


def _proposals_from(resolved: Resolution, gathered: Gathered) -> list[FamilyProposal]:
    proposals = [
        _build_proposal(
            members,
            state="PROPOSED",
            reason=None,
            workbook_names=gathered.workbook_names,
            sheets=gathered.sheets,
            sheet_dimensions=gathered.sheet_dimensions,
            tables_of=gathered.tables_of,
            fields_of=gathered.fields_of,
            shapes_of=gathered.shapes_of,
        )
        for members in sorted(resolved.families, key=lambda c: sorted(c)[0])
    ]
    proposals.extend(
        _build_proposal(
            members,
            state="SINGLETON",
            reason=why,
            workbook_names=gathered.workbook_names,
            sheets=gathered.sheets,
            sheet_dimensions=gathered.sheet_dimensions,
            tables_of=gathered.tables_of,
            fields_of=gathered.fields_of,
            shapes_of=gathered.shapes_of,
        )
        for members, why in sorted(resolved.singletons.items(), key=lambda kv: sorted(kv[0])[0])
    )
    return proposals


def _build_proposal(
    members: frozenset[str],
    *,
    state: str,
    reason: str | None,
    workbook_names: Mapping[str, dict[str, Any]],
    sheets: Mapping[str, set[str]],
    sheet_dimensions: Mapping[str, frozenset[str]],
    tables_of: Mapping[str, set[str]],
    fields_of: Mapping[str, set[str]],
    shapes_of: Mapping[str, set[str]],
) -> FamilyProposal:
    ordered_members = tuple(sorted(members))
    member_sheets = [s for m in ordered_members for s in sheets.get(m, set())]
    grain = candidate_grain(sheet_dimensions.get(s, frozenset()) for s in member_sheets)
    evidence = family_evidence(ordered_members, tables_of, fields_of, shapes_of)
    return FamilyProposal(
        id=new_ulid(),
        name=_family_name(ordered_members, workbook_names),
        state=state,
        members=ordered_members,
        grain=grain,
        evidence=evidence,
        reason=reason,
    )


def _derived_edge_id(*parts: str) -> str:
    """A ULID-shaped id derived from content, so a re-run upserts the same SHARES_LINEAGE
    edge rather than accumulating a duplicate for the same pair every run.

    Not ``harvest.identity.derive_id``: that function derives *source object* identity from
    an adapter's fragment key. This is a platform-derived edge with no source object behind
    it at all, so it gets its own small, honestly-named namespace rather than borrowing one
    whose docstring says something else.
    """
    alphabet = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    digest = hashlib.blake2b(
        "\x1f".join(parts).encode("utf-8"), key=b"astra.cartographer.derived-edge.v1", digest_size=16
    ).digest()
    value = int.from_bytes(digest, "big")
    timestamp = (value >> 80) & ((1 << 47) - 1)
    randomness = value & ((1 << 80) - 1)

    def encode(n: int, length: int) -> str:
        out = [""] * length
        for index in range(length - 1, -1, -1):
            out[index] = alphabet[n & 0x1F]
            n >>= 5
        return "".join(out)

    return encode(timestamp, 10) + encode(randomness, 16)


__all__ = [
    "CARTOGRAPHER_OWNED_STATES",
    "DEFAULT_MIN_FAMILY_SIZE",
    "DEFAULT_THRESHOLD",
    "Cartographer",
    "ClusteringResult",
    "FamilyEvidence",
    "FamilyProposal",
    "Resolution",
    "agglomerative_clusters",
    "candidate_grain",
    "count_families",
    "family_evidence",
    "get_family",
    "list_families",
    "resolve_undersized",
]
