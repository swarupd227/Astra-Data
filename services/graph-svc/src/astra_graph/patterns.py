"""The Pattern Library — specification §4.3/§9.3, stories S5.5.1 and S5.5.2.

    "As a platform engineer, I want a proved C3 transformation to become a candidate
    pattern automatically, so that the platform gets faster and more deterministic as the
    programme runs. When a GENERATED_PROVED artefact passes proof, its (source AST shape,
    target template, guards) tuple is generalised and stored as a Pattern in CANDIDATE
    state, keyed by AST shape hash. Promotion CANDIDATE -> ACTIVE requires N distinct proof
    passes (default 5), zero failures, and a Platform Engineer approval (MA-11, L2). ACTIVE
    patterns are applied deterministically ahead of any model call; the class of the field
    is re-evaluated to C2 with pattern_ref."

**What this module is.** Three real mechanisms, in the order the AC names them.
`generalise_from_proof` turns one successful `generation.generate_c3_field` call into a
`Pattern` node — a new CANDIDATE the first time a shape is proved, or another real proof
observation against the CANDIDATE/ACTIVE pattern that shape already has, never a duplicate.
`promote_pattern` re-checks the AC's own two objective conditions against the real
observation history (never trusting a caller's own count) before a Platform Engineer's
approval can flip CANDIDATE to ACTIVE. `apply_active_pattern` is the payoff: called from
`generate_c3_field` *before* any model is ever reached, it renders an ACTIVE pattern's own
`target_template` against this specific calculation's real field/parameter references and
writes a Measure directly — the field's own `class` becomes C2, its `pattern_ref` the real
Pattern node, and the model call this AST shape used to need never happens again.

**"Keyed by AST shape hash."** `context.signature.ast_shape` — fixed by this same story to
finally understand the real Tableau/adapter-sdk wire AST (see that module's own docstring)
— is the shape; `context.canonical.context_hash` over it would be the literal hash the AC's
own words name. Patterns are matched by shape equality directly (`signature.matches`,
already built for the Transpiler's own context contract, S1.3.1), the same real-not-hashed
comparison `generation._matching_patterns`/`context.assembler._patterns` already make — a
hash is only useful as an index key, not as the comparison itself, and nothing here needs
one: `find_matching_pattern` reads the Pattern Library whole (a library, not an estate; the
same `MAX_PATTERNS`-bounded reasoning `context.assembler` already gives this exact read).

**No real Migration Unit exists, so a proof's own CalculatedField id is the disclosed proxy
for §9.3's own "applied to at least five distinct MUs."** Confirmed, not assumed, the
identical finding story S5.4.1 already made against this same spec section's own MU
references: §4.1.1 declares no `MigrationUnit` node, and no story before this one has ever
created a real MU record. A `pattern_observation` row's own `calc_id` is the nearest real
thing this platform has to "which MU proved this" — one calculation, one real source
record — the same "attach to what already exists" choice the product owner made explicitly
for S5.4.1's own MU-shaped gap.

**Guards are descriptive, not evaluated — the identical footing `rules.RuleMeta.guards`
already has.** §9.3 also says pattern matching is "guarded on types and model context";
building a real guard-evaluation engine is not attempted here, matching stays exact-shape
only, and `guards` is inferred, modestly and honestly, from real `Field`/`Parameter`
datatypes this platform can actually resolve for a capture (`"a,b numeric"`-style text,
§4.3's own worked example) — never fabricated for a capture it cannot resolve.

**"Passed proof" inherits this epic's own established, disclosed proxy, a fourth time.**
No Arbiter (E7) exists anywhere in this codebase (ADR 0036/0038/0039/0040's own repeated
finding); a `GENERATED_PROVED` artefact "passing proof" already means clearing the real,
checkable rungs (schema + parse) with rungs 3/4 disclosed, unconditional passes —
`generalise_from_proof` generalises from exactly that, not from a real parity verdict this
platform cannot grant.

**Retirement (S5.5.2) is automatic, not approved — MA-12, ceiling L4, "automatic on
failure threshold."** Unlike promotion (a Platform Engineer's own L2 approve-first
action), `record_failure_and_maybe_retire` performs the retirement itself the moment a
failure attributed to a currently-ACTIVE pattern crosses the threshold — no route, no
human in the loop, matching §13.2's own ceiling for this specific action class exactly (a
deliberate asymmetry with MA-11, not an oversight).

**The threshold is the spec's own number, not the backlog's paraphrase — spec wins on
disagreement (this repository's own standing rule, applied identically at S5.1.1).** The
backlog's own AC text says "default 2 in 100 applications"; §9.3's own worked text says
"above a threshold (default 3 failures or a pass rate below 0.97 over 30 applications)" —
a materially different number and shape (an absolute trip-wire *or* a ratio, not a single
ratio). `evaluate_retirement` implements the spec's own dual condition, both thresholds
kept overridable exactly as `calibration.build_report`'s own `floor` parameter already is.

**No real Migration Unit exists, so "every MU that used it is flagged ... for re-proof"
(§9.3) is disclosed as "every Measure this Pattern produced that this platform can still
find" — the same MU-shaped gap S5.4.1/S5.5.1 already found, extended a third time.**
Nothing in this codebase ever marks an artefact ACCEPTED (no real G3 gate exists either,
S9.1.1/S9.1.2's own later scope), so the AC's own "not yet ACCEPTED" is honestly
unconditional here: every live Measure citing the retired pattern is re-queued.
"Re-queued for regeneration" is concrete, not a flag nobody acts on: the stale Measure is
retired (`GraphWriter.retire_node`) and the source `CalculatedField`'s own `class`/
`pattern_ref` are reverted to what the plain, pattern-unaware classifier
(`classify.classify`) says fresh — without the now-RETIRED pattern to match, that is
almost always C3 again, which is precisely what makes the field eligible for a real
`generate_c3_field` call the next time anyone (or a scheduler, once one exists) asks.

**"An event is raised" is a real notice, sharing the outbox the identical way
`events.source_drift` already does** (S1.2.4's own precedent) — `EventType.PATTERN_RETIRED`,
carrying the retirement reason and every re-queued Measure id in one place, not a mutation
a consumer would have to infer from a Pattern's own property diff.

**What this module is not.** It does not build a Pattern Library *console screen* or a
Parity Dashboard "retirements feed" — this story's own acceptance criteria asks for the
mechanism and the event, not a screen, the identical scope boundary S5.2.1/S5.2.2 already
drew for the Pattern Library's own promotion pipeline before either of these stories existed
to build it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import asyncpg

from .classify import CLASSIFIER_VERSION, ClassificationContext, classify
from .context.canonical import context_hash
from .context.contract import ContractName
from .context.signature import SignatureError, ast_shape, capture_identifiers, matches, signature_of
from .events import pattern_retired
from .graph.queries import EDGE_INDEX_TABLE, NODE_INDEX_TABLE
from .ids import new_ulid
from .lineage import children, hydrate
from .principal import Principal
from .provenance import AgentMode, ProvenanceStore, new_record
from .rules import dax_sanity_check
from .writes import EdgeWrite, GraphWriter, NodeWrite

PATTERN_OBSERVATION_TABLE = "public.pattern_observation"

#: §4.3's own "a library, not an estate" scale — the identical bound
#: `context.assembler`'s own pattern-matching read already uses.
MAX_PATTERNS = 5_000

#: The AC's own default -- "N distinct proof passes (default 5)".
PROMOTION_THRESHOLD_DEFAULT = 5

#: §9.3's own dual retirement condition, verbatim -- see the module docstring's own
#: "the threshold is the spec's own number" section for why these, not the backlog AC's
#: own paraphrased "2 in 100".
DEFAULT_FAILURE_COUNT_THRESHOLD = 3
DEFAULT_PASS_RATE_THRESHOLD = 0.97
MIN_APPLICATIONS_FOR_RATE_CHECK = 30

_AGENT = "transpiler"
_AGENT_VERSION = "0.1.0"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class PatternPromotionError(Exception):
    """A promotion was requested that the recorded evidence does not support."""


@dataclass(frozen=True, slots=True)
class PatternMatch:
    pattern_id: str
    class_: str
    target_template: str
    promotion_state: str


async def find_matching_pattern(
    pool: asyncpg.Pool, graph_name: str, formula_ast: Any
) -> PatternMatch | None:
    """Any live, non-RETIRED Pattern whose `source_signature` matches this AST's shape
    (§9.3: "Patterns are matched by AST shape"). ACTIVE wins over CANDIDATE if, somehow,
    two patterns exist for one shape (`generalise_from_proof` reuses an existing pattern
    rather than ever creating a second one for the same shape, so this should not arise in
    practice; a deterministic tiebreak is still the honest choice if it ever does)."""
    if not isinstance(formula_ast, dict):
        return None
    try:
        shape = ast_shape(formula_ast)
    except SignatureError:
        return None

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""SELECT id FROM {NODE_INDEX_TABLE}
             WHERE graph = $1 AND kind = 'node' AND label = 'Pattern' AND retired_at IS NULL
             LIMIT {MAX_PATTERNS}""",
            graph_name,
        )
        if not rows:
            return None
        patterns = await hydrate(conn, graph_name, "Pattern", [row["id"] for row in rows])

    live = [
        (pattern_id, props)
        for pattern_id, props in patterns.items()
        if props.get("promotion_state") != "RETIRED"
        and matches(props.get("source_signature"), shape=shape, adapter="tableau")
    ]
    if not live:
        return None
    live.sort(key=lambda item: item[1].get("promotion_state") != "ACTIVE")
    pattern_id, props = live[0]
    return PatternMatch(
        pattern_id=pattern_id,
        class_=str(props.get("class")),
        target_template=str(props.get("target_template")),
        promotion_state=str(props.get("promotion_state")),
    )


def render_target(target_template: str, captures: Mapping[str, str]) -> str:
    """Substitute a pattern's own `{a}`/`{b}`/... placeholders with this calculation's
    real field/parameter references. Any other `{token}` in the template (`{table}`,
    `rules.py`'s own disclosed, unresolved model-context placeholder — §4.3's worked
    example ships the identical convention) is left untouched: it names no capture of
    *this* AST, so substituting it would be a guess this module has no basis for."""
    rendered = target_template
    for placeholder, identifier in captures.items():
        rendered = rendered.replace("{" + placeholder + "}", f"[{identifier}]")
    return rendered


def _abstract_template(dax: str, captures: Mapping[str, str]) -> str:
    """The reverse of `render_target`: turn one proved calculation's own generated DAX
    into a reusable template by replacing each captured identifier's own bracketed
    reference (`[Notional]`) with its placeholder (`{a}`).

    **What this cannot do.** A text substitution, not a DAX parser: if the model's own
    output does not use this platform's `[Name]` bracket convention for a captured
    reference verbatim (e.g. it renamed or re-derived the identifier), that occurrence
    stays baked into the template as literal text rather than becoming a placeholder — an
    honest limit of generalising from text, not a fabricated abstraction. Identifiers are
    substituted longest-first so one name being a substring of another's (`Notional`
    inside `NotionalTotal`) cannot corrupt an unrelated occurrence.
    """
    template = dax
    for placeholder, identifier in sorted(captures.items(), key=lambda kv: -len(kv[1])):
        template = template.replace(f"[{identifier}]", "{" + placeholder + "}")
    return template


async def _infer_guards(
    pool: asyncpg.Pool, graph_name: str, calc_id: str, captures: Mapping[str, str]
) -> list[str]:
    """A modest, real guard for each captured identifier this platform can resolve to a
    real `Field`/`Parameter` with a known datatype (`"a is string"`) — §4.3's own worked
    example (`'a,b numeric'`), the identical "descriptive, not evaluated" footing
    `rules.RuleMeta.guards` already has. An identifier this platform cannot resolve (or
    whose datatype is unknown) earns no guard, rather than a fabricated one.
    """
    async with pool.acquire() as conn:
        field_hits = await children(conn, graph_name, [calc_id], "DEPENDS_ON", "Field")
        param_hits = await children(conn, graph_name, [calc_id], "DEPENDS_ON", "Parameter")
        field_ids: set[str] = field_hits.get(calc_id, set())
        param_ids: set[str] = param_hits.get(calc_id, set())
        fields = await hydrate(conn, graph_name, "Field", list(field_ids)) if field_ids else {}
        parameters = (
            await hydrate(conn, graph_name, "Parameter", list(param_ids)) if param_ids else {}
        )

    datatype_by_name: dict[str, str] = {}
    for props in (*fields.values(), *parameters.values()):
        name, datatype = props.get("name"), props.get("datatype")
        if name and datatype:
            datatype_by_name[str(name)] = str(datatype)

    return [
        f"{placeholder} is {datatype_by_name[identifier]}"
        for placeholder, identifier in sorted(captures.items())
        if identifier in datatype_by_name
    ]


async def record_observation(
    pool: asyncpg.Pool,
    graph_name: str,
    *,
    pattern_id: str,
    calc_id: str,
    observed_pass: bool,
    source: str,
    created_by: str,
) -> None:
    """Append-only, the identical footing `calibration.PostgresCalibrationStore.record`
    already set: every observation is a new row, never an update, so promotion eligibility
    is always checked against the complete history this platform has actually seen."""
    async with pool.acquire() as conn:
        await conn.execute(
            f"""INSERT INTO {PATTERN_OBSERVATION_TABLE}
             (id, graph, pattern_id, calc_id, observed_pass, source, created_by, recorded_at)
             VALUES ($1, $2, $3, $4, $5, $6, $7, now())""",
            f"patobs_{new_ulid()}", graph_name, pattern_id, calc_id, observed_pass, source, created_by,
        )


@dataclass(frozen=True, slots=True)
class RetirementCheck:
    """§9.3's own dual retirement condition, evaluated against one pattern's real
    observation counts."""

    should_retire: bool
    reason: str
    failures: int
    applications: int
    pass_rate: float | None


def evaluate_retirement(
    *,
    failures: int,
    applications: int,
    failure_count_threshold: int = DEFAULT_FAILURE_COUNT_THRESHOLD,
    pass_rate_threshold: float = DEFAULT_PASS_RATE_THRESHOLD,
    min_applications: int = MIN_APPLICATIONS_FOR_RATE_CHECK,
) -> RetirementCheck:
    """Pure arithmetic, kept separate from the store exactly as `calibration.build_report`
    is: "above a threshold (default 3 failures or a pass rate below 0.97 over 30
    applications)" (§9.3), either condition sufficient on its own — an absolute trip-wire
    for a pattern that fails outright, a ratio for one that fails often without yet
    reaching the absolute count."""
    pass_rate = (applications - failures) / applications if applications else None

    if failures >= failure_count_threshold:
        return RetirementCheck(
            True, f"{failures} recorded failures reached the threshold of {failure_count_threshold}",
            failures, applications, pass_rate,
        )
    if applications >= min_applications and pass_rate is not None and pass_rate < pass_rate_threshold:
        return RetirementCheck(
            True,
            f"pass rate {pass_rate:.3f} fell below {pass_rate_threshold} over "
            f"{applications} applications (>= {min_applications} required)",
            failures, applications, pass_rate,
        )
    return RetirementCheck(False, "within threshold", failures, applications, pass_rate)


async def _retirement_check(
    pool: asyncpg.Pool,
    graph_name: str,
    pattern_id: str,
    *,
    failure_count_threshold: int,
    pass_rate_threshold: float,
    min_applications: int,
) -> RetirementCheck:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""SELECT observed_pass FROM {PATTERN_OBSERVATION_TABLE}
             WHERE graph = $1 AND pattern_id = $2""",
            graph_name, pattern_id,
        )
    applications = len(rows)
    failures = sum(1 for row in rows if not row["observed_pass"])
    return evaluate_retirement(
        failures=failures, applications=applications,
        failure_count_threshold=failure_count_threshold,
        pass_rate_threshold=pass_rate_threshold, min_applications=min_applications,
    )


async def _encoding_worksheets(conn: asyncpg.Connection, graph_name: str, calc_id: str) -> list[str]:
    """Copied from `generation.py`'s own private helper of the same name -- the same
    small, self-contained duplication `classify.py`/`rules.py`/`generation.py` already
    each carry their own copy of `_current_version`/`_writable_node_properties` for,
    rather than a cross-import that would cycle back to `generation.py` (which already
    imports this module)."""
    rows = await conn.fetch(
        f"""SELECT e.from_id AS worksheet_id
         FROM {EDGE_INDEX_TABLE} e
         JOIN {NODE_INDEX_TABLE} n ON n.id = e.from_id AND n.kind = 'node'
          AND n.graph = $1 AND n.label = 'Worksheet' AND n.retired_at IS NULL
         WHERE e.graph = $1 AND e.label = 'ENCODES' AND e.to_id = $2 AND e.retired_at IS NULL""",
        graph_name, calc_id,
    )
    return [row["worksheet_id"] for row in rows]


async def _classification_context(
    conn: asyncpg.Connection, graph_name: str, calc_id: str
) -> ClassificationContext:
    """Copied from `generation.py`'s own private helper — see `_encoding_worksheets`'s own
    note on why."""
    param_deps = await children(conn, graph_name, [calc_id], "DEPENDS_ON", "Parameter")
    worksheet_ids = await _encoding_worksheets(conn, graph_name, calc_id)
    worksheets = await hydrate(conn, graph_name, "Worksheet", worksheet_ids)
    resolved = any(w.get("rows_shelf") or w.get("cols_shelf") for w in worksheets.values())
    return ClassificationContext(
        has_parameter_dependency=bool(param_deps.get(calc_id)),
        table_calc_addressing_resolved=resolved,
    )


async def _requeue_measures_for_retired_pattern(
    pool: asyncpg.Pool, graph_name: str, writer: GraphWriter, *, pattern_id: str, principal: Principal
) -> tuple[str, ...]:
    """The AC's own bullet 2: "retiring a pattern re-queues the artefacts it produced that
    have not yet been ACCEPTED for regeneration." See the module docstring's own
    "no real Migration Unit exists" section for why every live Measure citing this pattern
    qualifies, unconditionally. Each one is retired (it is no longer trustworthy output),
    and its source `CalculatedField` is reclassified by the plain, pattern-unaware
    classifier — without this pattern to match, that real verdict is what makes the field
    eligible for generation again."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""SELECT id FROM {NODE_INDEX_TABLE}
             WHERE graph = $1 AND kind = 'node' AND label = 'Measure' AND retired_at IS NULL""",
            graph_name,
        )
        measures = await hydrate(conn, graph_name, "Measure", [row["id"] for row in rows]) if rows else {}

    affected = {
        measure_id: props for measure_id, props in measures.items()
        if props.get("pattern_ref") == pattern_id
    }

    requeued: list[str] = []
    for measure_id, measure_props in affected.items():
        await writer.retire_node(
            measure_id,
            reason=f"source pattern {pattern_id} was retired; regeneration required",
            principal=principal,
        )
        requeued.append(measure_id)

        calc_id = measure_props.get("source_calc_ref")
        if not calc_id:
            continue
        async with pool.acquire() as conn:
            calc = (await hydrate(conn, graph_name, "CalculatedField", [calc_id])).get(calc_id)
            if calc is None:
                continue
            context = await _classification_context(conn, graph_name, calc_id)
        result = classify(calc.get("formula_ast"), context=context)
        await writer.set_node_properties(
            calc_id,
            {
                "class": result.class_,
                "pattern_ref": result.rule_id,
                "reason": result.reason,
                "classifier_version": CLASSIFIER_VERSION,
            },
            principal=principal,
        )

    return tuple(requeued)


async def record_failure_and_maybe_retire(
    pool: asyncpg.Pool,
    graph_name: str,
    writer: GraphWriter,
    *,
    pattern_id: str,
    calc_id: str,
    source: str,
    principal: Principal,
    failure_count_threshold: int = DEFAULT_FAILURE_COUNT_THRESHOLD,
    pass_rate_threshold: float = DEFAULT_PASS_RATE_THRESHOLD,
    min_applications: int = MIN_APPLICATIONS_FOR_RATE_CHECK,
) -> RetirementCheck | None:
    """The single place a proof failure against any pattern is recorded (both
    `generation.generate_c3_field`'s own model-served ladder and `apply_active_pattern`'s
    own deterministic-render sanity check call this rather than `record_observation`
    directly) — bullet 1: increments the real observation history and the Pattern's own
    disclosed `failure_count` snapshot for *any* promotion_state, then, only for a
    currently-ACTIVE pattern, checks §9.3's own retirement condition and performs the
    retirement (state, provenance, the re-queue in bullet 2, and the notice) the moment it
    is crossed — automatic, MA-12, ceiling L4, no approval step. Returns `None` when the
    pattern no longer exists or was never ACTIVE (nothing to retire); the check performed
    otherwise, whether or not it actually retired the pattern.
    """
    await record_observation(
        pool, graph_name, pattern_id=pattern_id, calc_id=calc_id,
        observed_pass=False, source=source, created_by=principal.value,
    )

    async with pool.acquire() as conn:
        pattern = (await hydrate(conn, graph_name, "Pattern", [pattern_id])).get(pattern_id)
    if pattern is None:
        return None

    await writer.set_node_properties(
        pattern_id, {"failure_count": int(pattern.get("failure_count") or 0) + 1}, principal=principal,
    )
    if pattern.get("promotion_state") != "ACTIVE":
        return None

    check = await _retirement_check(
        pool, graph_name, pattern_id,
        failure_count_threshold=failure_count_threshold,
        pass_rate_threshold=pass_rate_threshold, min_applications=min_applications,
    )
    if not check.should_retire:
        return check

    requeued = await _requeue_measures_for_retired_pattern(
        pool, graph_name, writer, pattern_id=pattern_id, principal=principal,
    )
    provenance = dict(pattern.get("provenance") or {})
    provenance.update(retired_at=_now(), retirement_reason=check.reason)
    await writer.set_node_properties(
        pattern_id, {"promotion_state": "RETIRED", "provenance": provenance}, principal=principal,
    )
    await writer.append_event(
        pattern_retired(
            source=writer.event_source, pattern_id=pattern_id, reason=check.reason,
            requeued_measure_ids=requeued, principal=principal,
        )
    )
    return check


async def generalise_from_proof(
    pool: asyncpg.Pool,
    graph_name: str,
    writer: GraphWriter,
    *,
    calc_id: str,
    formula_ast: Any,
    dax: str,
    class_: str,
    principal: Principal,
) -> str | None:
    """The AC's own bullet 1: "When a GENERATED_PROVED artefact passes proof, its (source
    AST shape, target template, guards) tuple is generalised and stored as a Pattern in
    CANDIDATE state." Reuses an existing pattern for an unchanged shape — recording
    another real proof observation against it, which is how a CANDIDATE ever accumulates
    the AC's own "N distinct proof passes" — rather than ever creating a second Pattern
    for the same shape. Returns `None` when the AST has no computable shape rather than
    guessing at one.
    """
    if not isinstance(formula_ast, dict):
        return None
    try:
        shape = ast_shape(formula_ast)
    except SignatureError:
        return None
    captures = capture_identifiers(formula_ast)

    existing = await find_matching_pattern(pool, graph_name, formula_ast)
    if existing is not None:
        await record_observation(
            pool, graph_name, pattern_id=existing.pattern_id, calc_id=calc_id,
            observed_pass=True, source="GENERATED_PROVED", created_by=principal.value,
        )
        return existing.pattern_id

    guards = await _infer_guards(pool, graph_name, calc_id, captures)
    pattern_id = new_ulid()
    await writer.write_nodes(
        [
            NodeWrite(
                type="Pattern",
                id=pattern_id,
                properties={
                    "name": f"pattern_{shape}"[:80],
                    "class": class_,
                    "source_signature": signature_of(formula_ast, adapter="tableau"),
                    "target_template": _abstract_template(dax, captures),
                    "guards": guards,
                    "provenance": {"origin": "PROMOTED_FROM_LLM", "first_seen": calc_id},
                    "promotion_state": "CANDIDATE",
                    "pass_count": 1,
                },
            )
        ],
        principal=principal,
    )
    await record_observation(
        pool, graph_name, pattern_id=pattern_id, calc_id=calc_id,
        observed_pass=True, source="GENERATED_PROVED", created_by=principal.value,
    )
    return pattern_id


@dataclass(frozen=True, slots=True)
class PromotionStatus:
    pattern_id: str
    promotion_state: str
    distinct_passing_calcs: int
    has_failure: bool
    threshold: int
    eligible: bool
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "pattern_id": self.pattern_id,
            "promotion_state": self.promotion_state,
            "distinct_passing_calcs": self.distinct_passing_calcs,
            "has_failure": self.has_failure,
            "threshold": self.threshold,
            "eligible": self.eligible,
            "reason": self.reason,
        }


async def promotion_status(
    pool: asyncpg.Pool, graph_name: str, pattern_id: str, *, threshold: int = PROMOTION_THRESHOLD_DEFAULT
) -> PromotionStatus:
    """The AC's own two objective conditions, checked against the real observation
    history — never a maintained counter that could drift from it."""
    async with pool.acquire() as conn:
        pattern = (await hydrate(conn, graph_name, "Pattern", [pattern_id])).get(pattern_id)
        if pattern is None:
            raise PatternPromotionError(f"no Pattern with id '{pattern_id}'")
        rows = await conn.fetch(
            f"""SELECT calc_id, observed_pass FROM {PATTERN_OBSERVATION_TABLE}
             WHERE graph = $1 AND pattern_id = $2""",
            graph_name, pattern_id,
        )

    passing_calcs = {row["calc_id"] for row in rows if row["observed_pass"]}
    has_failure = any(not row["observed_pass"] for row in rows)
    state = str(pattern.get("promotion_state"))
    distinct = len(passing_calcs)
    eligible = state == "CANDIDATE" and distinct >= threshold and not has_failure

    if state != "CANDIDATE":
        reason = f"already {state}"
    elif has_failure:
        reason = "at least one recorded proof failure"
    elif distinct < threshold:
        reason = f"only {distinct} of {threshold} required distinct proof passes"
    else:
        reason = "eligible"

    return PromotionStatus(
        pattern_id=pattern_id, promotion_state=state, distinct_passing_calcs=distinct,
        has_failure=has_failure, threshold=threshold, eligible=eligible, reason=reason,
    )


async def promote_pattern(
    pool: asyncpg.Pool,
    graph_name: str,
    writer: GraphWriter,
    *,
    pattern_id: str,
    principal: Principal,
    threshold: int = PROMOTION_THRESHOLD_DEFAULT,
) -> dict[str, Any]:
    """CANDIDATE -> ACTIVE: MA-11, autonomy ceiling L2 ("approve-first" — §13.2) — the
    Platform Engineer's own approval names the action, but eligibility is re-checked here,
    server-side, rather than trusted from the caller (`PatternPromotionError` if not)."""
    status = await promotion_status(pool, graph_name, pattern_id, threshold=threshold)
    if not status.eligible:
        raise PatternPromotionError(
            f"Pattern '{pattern_id}' is not eligible for promotion: {status.reason}"
        )

    async with pool.acquire() as conn:
        pattern = (await hydrate(conn, graph_name, "Pattern", [pattern_id]))[pattern_id]

    provenance = dict(pattern.get("provenance") or {})
    provenance.update(promoted_at=_now(), approved_by=principal.value)
    updated = await writer.set_node_properties(
        pattern_id,
        {
            "promotion_state": "ACTIVE",
            "provenance": provenance,
            "pass_count": status.distinct_passing_calcs,
        },
        principal=principal,
    )
    return dict(updated["properties"])


async def apply_active_pattern(
    pool: asyncpg.Pool,
    graph_name: str,
    writer: GraphWriter,
    provenance_store: ProvenanceStore,
    *,
    calc_id: str,
    calc_properties: Mapping[str, Any],
    pattern: PatternMatch,
    graph_version: int,
    principal: Principal,
) -> str | None:
    """The AC's own bullet 3: "ACTIVE patterns are applied deterministically ahead of any
    model call; the class of the field is re-evaluated to C2 with pattern_ref." Renders
    the pattern's own `target_template` against this calculation's real captures, writes a
    real Measure/MAPS_TO/DETERMINISTIC provenance record, and re-evaluates the source
    `CalculatedField`'s own `class`/`pattern_ref` in place — the one real record classify.py
    ever wrote it to. Returns the new Measure id on success.

    Returns `None` — recording a failure observation rather than raising — when the
    rendered DAX fails even `rules.dax_sanity_check`'s structural stand-in for real DAX
    parsing. Rare, since the template came from an already-proven artefact, but a real,
    checked possibility (a captured identifier's own bracketed form colliding oddly), not
    assumed away: the caller falls back to the normal model-call path rather than blocking
    a field on one pattern's own hiccup, and the failure counts toward §9.3's own
    automatic-retirement threshold (`record_failure_and_maybe_retire`, story S5.5.2) the
    identical way a model-served ladder failure already does.
    """
    formula_ast = calc_properties.get("formula_ast")
    captures = capture_identifiers(formula_ast) if isinstance(formula_ast, dict) else {}
    dax = render_target(pattern.target_template, captures)

    parse_error = dax_sanity_check(dax)
    if parse_error is not None:
        await record_failure_and_maybe_retire(
            pool, graph_name, writer, pattern_id=pattern.pattern_id, calc_id=calc_id,
            source="DETERMINISTIC_APPLICATION", principal=principal,
        )
        return None

    measure_id = new_ulid()
    provenance_id = f"prov_{new_ulid()}"
    await writer.write_nodes(
        [
            NodeWrite(
                type="Measure",
                id=measure_id,
                properties={
                    "name": str(calc_properties.get("name") or calc_id),
                    "dax": dax,
                    "source_calc_ref": calc_id,
                    "class": "C2",
                    "pattern_ref": pattern.pattern_id,
                    "provenance_ref": provenance_id,
                    "validation_state": (
                        "rung 2 (structural): balanced syntax and known DAX functions only "
                        "-- applied deterministically from an ACTIVE Pattern (§9.3), ahead "
                        "of any model call"
                    ),
                },
            )
        ],
        principal=principal,
    )
    await writer.write_edge(
        EdgeWrite(
            type="MAPS_TO", from_id=calc_id, to_id=measure_id,
            properties={"class": "C2", "pattern_ref": pattern.pattern_id},
        ),
        principal=principal,
    )
    record = new_record(
        id=provenance_id,
        artefact_kind="MEASURE",
        artefact_ref=measure_id,
        artefact_content_hash=context_hash(dax.encode("utf-8")),
        agent=_AGENT,
        agent_version=_AGENT_VERSION,
        mode=AgentMode.DETERMINISTIC,
        contract=ContractName.TRANSPILER_CALC,
        subject_id=calc_id,
        context_hash=context_hash(str(formula_ast).encode("utf-8")),
        graph_version=graph_version,
        model=None,
        pattern_ref=pattern.pattern_id,
        created_by=principal.value,
    )
    await provenance_store.record(record)

    await writer.set_node_properties(
        calc_id, {"class": "C2", "pattern_ref": pattern.pattern_id}, principal=principal,
    )
    await record_observation(
        pool, graph_name, pattern_id=pattern.pattern_id, calc_id=calc_id,
        observed_pass=True, source="DETERMINISTIC_APPLICATION", created_by=principal.value,
    )
    return measure_id


async def list_patterns(pool: asyncpg.Pool, graph_name: str) -> list[dict[str, Any]]:
    """Every live Pattern, with its pass/failure counts computed live from the real
    observation history — not the node's own `pass_count` snapshot — the identical
    "computed from the raw table on read" footing `calibration.report` already has."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""SELECT id FROM {NODE_INDEX_TABLE}
             WHERE graph = $1 AND kind = 'node' AND label = 'Pattern' AND retired_at IS NULL
             LIMIT {MAX_PATTERNS}""",
            graph_name,
        )
        pattern_ids = [row["id"] for row in rows]
        patterns = await hydrate(conn, graph_name, "Pattern", pattern_ids) if pattern_ids else {}
        obs_rows = (
            await conn.fetch(
                f"""SELECT pattern_id, calc_id, observed_pass FROM {PATTERN_OBSERVATION_TABLE}
                 WHERE graph = $1""",
                graph_name,
            )
            if pattern_ids
            else []
        )

    passing: dict[str, set[str]] = {}
    failing: dict[str, int] = {}
    for row in obs_rows:
        pid = row["pattern_id"]
        if row["observed_pass"]:
            passing.setdefault(pid, set()).add(row["calc_id"])
        else:
            failing[pid] = failing.get(pid, 0) + 1

    return [
        {
            "id": pattern_id,
            "name": props.get("name"),
            "class": props.get("class"),
            "promotion_state": props.get("promotion_state"),
            "target_template": props.get("target_template"),
            "guards": list(props.get("guards") or []),
            "distinct_passing_calcs": len(passing.get(pattern_id, ())),
            "failure_count": failing.get(pattern_id, 0),
            # `provenance` (not just the node's own point-in-time `pass_count`/
            # `failure_count`) is what actually carries a RETIRED pattern's own
            # `retired_at`/`retirement_reason` (story S5.5.2) -- worth the extra field on
            # every row so a retirement is visible without a second lookup.
            "provenance": dict(props.get("provenance") or {}),
        }
        for pattern_id, props in patterns.items()
    ]


__all__ = [
    "DEFAULT_FAILURE_COUNT_THRESHOLD",
    "DEFAULT_PASS_RATE_THRESHOLD",
    "MAX_PATTERNS",
    "MIN_APPLICATIONS_FOR_RATE_CHECK",
    "PATTERN_OBSERVATION_TABLE",
    "PROMOTION_THRESHOLD_DEFAULT",
    "PatternMatch",
    "PatternPromotionError",
    "PromotionStatus",
    "RetirementCheck",
    "apply_active_pattern",
    "evaluate_retirement",
    "find_matching_pattern",
    "generalise_from_proof",
    "list_patterns",
    "promote_pattern",
    "promotion_status",
    "record_failure_and_maybe_retire",
    "record_observation",
    "render_target",
]
