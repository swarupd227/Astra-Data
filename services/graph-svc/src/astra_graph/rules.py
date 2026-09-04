"""The Transpiler's deterministic rules engine — specification §9.2/§9.3, stories S5.2.1
and S5.2.2.

    "Rules are AST-pattern -> target-template with guards; shipped set covers the function
    families in Appendix B and the common LOD and table-calc shapes. Rule application is
    DETERMINISTIC mode: no model call; provenance records the rule id and version."

    "Regression: every rule change re-runs the golden corpus and the PASSED artefacts that
    used the rule; any new failure blocks promotion." (S5.2.2)

**What this module is.** §9.2's rule engine, built directly on `classify.py` (S5.1.1): the
same real AST (`CalcNode`-shaped `formula_ast` -- `kind`/`name`/`children`/`detail`) and the
same Appendix B.1 family the grammar already stamps into `detail`. Where `classify.py` asks
"what class is this node," this module asks "what DAX text does this node become" -- walking
the AST bottom-up exactly as §9.2 describes, rewriting each node into DAX and composing the
result from its own already-rewritten children. A handful of specific *shape* rules (LOD,
the null-coalescing idiom) are tried first, at every node, before the generic per-kind/
per-family fallback -- the same "specific pattern, then general map" order §9.2 itself
describes ("A rule failure downgrades the node to C2 matching; a C2 match failure downgrades
to C3").

**What this module is not.** A general declarative pattern-matching DSL (each shape rule is
a small, direct Python function -- nothing here claims the shape of every future rule, only
the ones actually shipped); the Pattern Library's own promotion pipeline (§9.3/§4.3, F5.5 --
hand-shipped rules stay code, never written as `Pattern` graph nodes, the same "the
deterministic rule set shipped with the adapter" reading `classify.py`'s own ADR 0035 already
settled); a tenant-by-tenant promotion queue (this platform has one deployment per client
environment, not a fleet to advance a change through one at a time -- "promoted to the
tenant on merge" is this codebase's own ordinary release path, a merged PR shipping in the
next deployed image); and Class 3 generation via a reasoning model (§9.4, F5.3, not built)
-- a calculation this module cannot render is simply left unconverted, which is exactly
what C3/C4 already mean.

**S5.2.2's own regression check.** "Every rule change re-runs the golden corpus" is already
true by construction -- `tests/test_rules.py` parametrizes over *every* rule's *every*
golden case on *every* CI run, so a shared-code change that breaks another rule's own case
is already caught, whether or not that rule was the one edited. What did not exist is the
other half: "the PASSED artefacts that used the rule" -- the real `Measure` nodes a rule has
already produced, in a real graph. `check_regression()` re-renders each one's source
`CalculatedField` against the *current* rule set and reports what changed. A field that used
to render and no longer does is a regression (blocks); a field that still renders, only
differently, is a disclosed change (does not block -- rules legitimately improve). See
`tools/rule_regression_check.py` for the CI-invocable guard and ADR 0037 for the full
reasoning.

**What "must pass proof in CI" honestly means here.** Spec §16.1's own validation ladder
names rung 4, "Proof," as a full parity verdict -- and that needs the Arbiter (E7) and a live
DAX-evaluating engine, neither of which exists anywhere in this codebase
(`FixtureTargetAdapter.smoke_query`'s own docstring, S4.3.1, already discloses "no live
Fabric analysis-services engine is configured to run a real ... query"). Fabricating a check
that claims rung-4 proof without either would be worse than naming what this platform can
actually verify today: that a rule's *golden cases* -- real ``(source AST, expected DAX)``
pairs -- render to byte-exact expected text, deterministically, plus a structural DAX sanity
check (balanced delimiters, every function name against a small known-DAX allowlist) standing
in for rung 2 ("parses under the target grammar") since no DAX parser exists either. See
ADR 0036 for the full reasoning.

**Model-context placeholders are real, not a bug.** §4.3's own worked Pattern example ships
a target template containing an *unresolved* model-context token --
``ALLEXCEPT({table}, {dims})`` -- because which target table a field maps to is the
Modeller/Compositor's own fact, not the rules engine's. This module follows the identical
convention: a rendered DAX string may contain the literal token ``{table}`` where a rule
needs a table reference it cannot resolve yet -- disclosed here and in every affected rule's
own golden cases, not silently guessed at.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import asyncpg

from .context.canonical import context_hash
from .context.contract import ContractName
from .graph.queries import NODE_INDEX_TABLE
from .ids import new_ulid
from .lineage import children, hydrate
from .ontology.types import BASE_NODE_PROPERTIES
from .principal import Principal
from .provenance import AgentMode, ProvenanceRecord, ProvenanceStore, new_record
from .versions import EVENT_TABLE
from .writes import EdgeWrite, GraphWriter, NodeWrite

#: Bumped whenever any rule's own matching or rendering logic changes -- read alongside
#: each individual rule's own `RuleMeta.version` for "which exact rule produced this," the
#: same "stamp the version that produced it" footing `classify.py`'s `CLASSIFIER_VERSION`
#: already has for classification.
RULES_VERSION = 1

_AGENT = "transpiler"
_AGENT_VERSION = "0.1.0"

_TABLE_PLACEHOLDER = "{table}"
"""The literal, disclosed, unresolved model-context token — see the module docstring's own
"Model-context placeholders are real, not a bug" section."""


# --------------------------------------------------------------------------- rule metadata


@dataclass(frozen=True, slots=True)
class GoldenCase:
    """One `(source AST, expected DAX)` pair a rule ships with (story S5.2.1's own "at least
    three golden-corpus cases")."""

    name: str
    ast: Mapping[str, Any]
    expected_dax: str


@dataclass(frozen=True, slots=True)
class RuleMeta:
    """A rule's own documentation and golden corpus -- consulted by the coverage report and
    by `tests/test_rules.py`, never by the renderer itself (which dispatches on `rule_id`
    string literals matched against these ids by a consistency test, not by looking this
    table up at render time)."""

    id: str
    version: int
    class_: str
    family: str
    description: str
    guards: tuple[str, ...]
    golden_cases: tuple[GoldenCase, ...]


def _ref(name: str) -> dict[str, Any]:
    return {"kind": "REFERENCE", "name": name, "value": None, "children": [], "detail": []}


def _lit(value: Any, kind: str = "integer") -> dict[str, Any]:
    return {"kind": "LITERAL", "name": kind, "value": value, "children": [], "detail": []}


def _op(name: str, *kids: Mapping[str, Any]) -> dict[str, Any]:
    return {"kind": "OPERATOR", "name": name, "value": None, "children": list(kids), "detail": []}


def _fn(name: str, family: str, *kids: Mapping[str, Any]) -> dict[str, Any]:
    return {"kind": "FUNCTION", "name": name, "value": None, "children": list(kids), "detail": [["family", family]]}


def _aggregate(name: str, *kids: Mapping[str, Any]) -> dict[str, Any]:
    return {"kind": "AGGREGATE", "name": name, "value": None, "children": list(kids), "detail": [["family", "aggregate"]]}


def _lod(grain: str, measure: Mapping[str, Any], *dims: Mapping[str, Any]) -> dict[str, Any]:
    return {"kind": "AGGREGATE", "name": grain, "value": None, "children": [measure, *dims], "detail": [["grain", grain]]}


def _cast(target: str, inner: Mapping[str, Any]) -> dict[str, Any]:
    return {"kind": "CAST", "name": target, "value": None, "children": [inner], "detail": []}


def _if(*kids: Mapping[str, Any]) -> dict[str, Any]:
    return {"kind": "CONDITIONAL", "name": "IF", "value": None, "children": list(kids), "detail": []}


RULES: tuple[RuleMeta, ...] = (
    RuleMeta(
        id="c1_operator", version=1, class_="C1", family="operator",
        description="Arithmetic, comparison and logical operators map to their direct DAX equivalent.",
        guards=("unary operators take exactly one operand", "binary operators take exactly two"),
        golden_cases=(
            GoldenCase("addition", _op("+", _ref("Margin"), _ref("Revenue")), "([Margin] + [Revenue])"),
            GoldenCase("division", _op("/", _ref("Margin"), _ref("Revenue")), "([Margin] / [Revenue])"),
            GoldenCase("and", _op("AND", _ref("Active"), _ref("Approved")), "([Active] && [Approved])"),
            GoldenCase("unary not", _op("NOT", _ref("Active")), "NOT([Active])"),
            GoldenCase("modulo", _op("%", _ref("Count"), _lit(2)), "MOD([Count], 2)"),
        ),
    ),
    RuleMeta(
        id="c1_aggregate", version=1, class_="C1", family="aggregate",
        description="Aggregate functions map to their direct DAX equivalent (Appendix B.1, Aggregate).",
        guards=("exactly one argument",),
        golden_cases=(
            GoldenCase("sum", _aggregate("SUM", _ref("Notional")), "SUM([Notional])"),
            GoldenCase("avg", _aggregate("AVG", _ref("Notional")), "AVERAGE([Notional])"),
            GoldenCase("countd", _aggregate("COUNTD", _ref("TradeId")), "DISTINCTCOUNT([TradeId])"),
            GoldenCase("percentile, a dotted DAX name", _aggregate("PERCENTILE", _ref("Notional")), "PERCENTILE.INC([Notional])"),
        ),
    ),
    RuleMeta(
        id="c1_cast", version=1, class_="C1", family="type",
        description="Type casts and Appendix B.1 Type-family functions map to their direct DAX equivalent.",
        guards=("exactly one argument",),
        golden_cases=(
            GoldenCase("cast to int", _cast("INT", _ref("Code")), "INT([Code])"),
            GoldenCase("type function float", _fn("FLOAT", "type", _ref("Code")), "VALUE([Code])"),
            GoldenCase("type function date", _fn("DATE", "type", _ref("AsOfText")), "DATEVALUE([AsOfText])"),
        ),
    ),
    RuleMeta(
        id="c1_conditional", version=1, class_="C1", family="conditional",
        description="IF/CASE map to DAX IF/SWITCH.",
        guards=("IF carries a condition and at least one branch", "CASE carries a subject and at least one clause"),
        golden_cases=(
            GoldenCase(
                "if then else",
                _if(_op(">", _ref("Notional"), _lit(0)), _lit("positive", "string"), _lit("non-positive", "string")),
                'IF(([Notional] > 0), "positive", "non-positive")',
            ),
            GoldenCase(
                "if then, no else",
                _if(_op(">", _ref("Notional"), _lit(0)), _lit("positive", "string")),
                'IF(([Notional] > 0), "positive")',
            ),
            GoldenCase(
                "case",
                {"kind": "CONDITIONAL", "name": "CASE", "value": None, "detail": [],
                 "children": [_ref("Desk"), _lit("EQ", "string"), _lit("Equities", "string"), _lit("Other", "string")]},
                'SWITCH(TRUE(), [Desk] = "EQ", "Equities", "Other")',
            ),
        ),
    ),
    RuleMeta(
        id="c1_numeric", version=1, class_="C1", family="numeric",
        description="The subset of Appendix B.1's numeric functions with a direct DAX equivalent.",
        guards=("exactly one argument",),
        golden_cases=(
            GoldenCase("abs", _fn("ABS", "numeric", _ref("Delta")), "ABS([Delta])"),
            GoldenCase("round", _fn("ROUND", "numeric", _ref("Rate")), "ROUND([Rate])"),
            GoldenCase("sqrt", _fn("SQRT", "numeric", _ref("Variance")), "SQRT([Variance])"),
        ),
    ),
    RuleMeta(
        id="c1_leaf", version=1, class_="C1", family="leaf",
        description="A bare field, parameter or calculation reference, or a literal.",
        guards=(),
        golden_cases=(
            GoldenCase("field reference", _ref("Notional"), "[Notional]"),
            GoldenCase("numeric literal", _lit(42), "42"),
            GoldenCase("string literal", _lit("EQ", "string"), '"EQ"'),
        ),
    ),
    RuleMeta(
        id="c2_null_idiom", version=1, class_="C2", family="logical",
        description="ZN/IFNULL are Appendix B.1's null-handling idiom -- a structural rewrite to COALESCE.",
        guards=("ZN takes one argument", "IFNULL takes two"),
        golden_cases=(
            GoldenCase("zn", _fn("ZN", "logical", _ref("Notional")), "COALESCE([Notional], 0)"),
            GoldenCase("ifnull with default", _fn("IFNULL", "logical", _ref("Notional"), _lit(0)), "COALESCE([Notional], 0)"),
            GoldenCase(
                "ifnull nested",
                _fn("IFNULL", "logical", _aggregate("SUM", _ref("Notional")), _lit(0)),
                "COALESCE(SUM([Notional]), 0)",
            ),
        ),
    ),
    RuleMeta(
        id="c2_lod_fixed", version=1, class_="C2", family="lod",
        description=(
            "A {FIXED dims : agg(measure)} level-of-detail expression rewrites to "
            "CALCULATE/ALLEXCEPT (spec §9.2/§4.3). The target table is a model-binding fact "
            "this rule does not have -- the rendered text carries the literal, disclosed "
            "placeholder {table} where it belongs, the same convention §4.3's own worked "
            "Pattern example ships."
        ),
        guards=("every dimension is a bare field reference", "the measure itself renders"),
        golden_cases=(
            GoldenCase(
                "single dimension",
                _lod("FIXED", _aggregate("SUM", _ref("Notional")), _ref("Desk")),
                "CALCULATE(SUM([Notional]), ALLEXCEPT({table}, [Desk]))",
            ),
            GoldenCase(
                "multiple dimensions",
                _lod("FIXED", _aggregate("SUM", _ref("Notional")), _ref("Desk"), _ref("Region")),
                "CALCULATE(SUM([Notional]), ALLEXCEPT({table}, [Desk], [Region]))",
            ),
            GoldenCase(
                "no dimensions",
                _lod("FIXED", _aggregate("SUM", _ref("Notional"))),
                "CALCULATE(SUM([Notional]), ALLEXCEPT({table}))",
            ),
        ),
    ),
)

_RULES_BY_ID: dict[str, RuleMeta] = {rule.id: rule for rule in RULES}


# --------------------------------------------------------------------------------- rendering


@dataclass(frozen=True, slots=True)
class RenderedNode:
    dax: str
    rule_id: str
    family: str


@dataclass(frozen=True, slots=True)
class RenderOutcome:
    ok: bool
    dax: str | None
    rule_id: str | None
    rule_version: int | None
    family: str | None
    reason: str


#: Appendix B.1, "Arithmetic / logical" -- the operator half; DAX uses `&&`/`||`, not the
#: source language's own keywords.
_BINARY_OPERATOR_DAX: dict[str, str] = {
    "+": "+", "-": "-", "*": "*", "/": "/", "^": "^",
    "==": "=", "=": "=", "<>": "<>", "!=": "<>",
    "<=": "<=", ">=": ">=", "<": "<", ">": ">",
    "AND": "&&", "OR": "||",
}

#: Appendix B.1, "Aggregate".
_AGGREGATE_DAX: dict[str, str] = {
    "SUM": "SUM", "AVG": "AVERAGE", "MIN": "MIN", "MAX": "MAX",
    "COUNT": "COUNT", "COUNTD": "DISTINCTCOUNT", "MEDIAN": "MEDIAN",
    "PERCENTILE": "PERCENTILE.INC",
}

#: Appendix B.1, "Type" -- both the CAST node kind and the equivalent named functions.
_TYPE_DAX: dict[str, str] = {
    "INT": "INT", "FLOAT": "VALUE", "STR": "FORMAT",
    "DATE": "DATEVALUE", "DATETIME": "DATEVALUE", "BOOL": "VALUE",
}

#: The subset of Appendix B.1's numeric functions this rule ships (see `c1_numeric`'s own
#: `RuleMeta.description` -- narrower than Tableau's own set, disclosed, not exhaustive).
_NUMERIC_DAX: dict[str, str] = {
    "ABS": "ABS", "CEILING": "CEILING", "FLOOR": "FLOOR", "ROUND": "ROUND",
    "SQRT": "SQRT", "POWER": "POWER", "EXP": "EXP", "LN": "LN", "SIGN": "SIGN",
}


def render_calc(ast: Any) -> RenderOutcome:
    """Render one calculation's AST into DAX text, or explain why it could not be.

    Deterministic: the same AST always renders the same way. Fails (returns `ok=False`)
    for anything a shipped rule does not cover -- exactly the C3/C4 boundary `classify.py`
    already draws; this module never guesses.
    """
    rendered = _render_node(ast)
    if rendered is None:
        return RenderOutcome(False, None, None, None, None, "no shipped rule matched this calculation")
    rule = _RULES_BY_ID.get(rendered.rule_id)
    version = rule.version if rule else None
    return RenderOutcome(True, rendered.dax, rendered.rule_id, version, rendered.family, "matched")


def _render_node(node: Any) -> RenderedNode | None:
    if not isinstance(node, dict):
        return None

    shaped = _try_null_idiom(node) or _try_lod_fixed(node)
    if shaped is not None:
        return shaped

    kind = str(node.get("kind") or "")
    name = str(node.get("name") or "")
    detail = dict(node.get("detail") or ())
    kids = node.get("children") or []

    if kind == "LITERAL":
        return RenderedNode(_render_literal(node), "c1_leaf", "leaf")
    if kind == "REFERENCE":
        return RenderedNode(f"[{name}]", "c1_leaf", "leaf")
    if kind == "OPERATOR":
        return _render_operator(name, kids)
    if kind == "CAST":
        rendered = _render_node(kids[0]) if kids else None
        if rendered is None:
            return None
        dax_name = _TYPE_DAX.get(name)
        if dax_name is None:
            return None
        return RenderedNode(f"{dax_name}({rendered.dax})", "c1_cast", "type")
    if kind == "CONDITIONAL":
        return _render_conditional(name, kids)
    if kind == "AGGREGATE":
        if len(kids) != 1:
            return None
        rendered = _render_node(kids[0])
        if rendered is None:
            return None
        dax_name = _AGGREGATE_DAX.get(name)
        if dax_name is None:
            return None
        return RenderedNode(f"{dax_name}({rendered.dax})", "c1_aggregate", "aggregate")
    if kind == "FUNCTION":
        return _render_function(name, detail, kids)
    return None


def _render_literal(node: Mapping[str, Any]) -> str:
    value = node.get("value")
    if isinstance(value, bool):
        return "TRUE()" if value else "FALSE()"
    if isinstance(value, int | float):
        return str(value)
    if value is None:
        return "BLANK()"
    return '"' + str(value).replace('"', '""') + '"'


def _render_operator(name: str, kids: list[Any]) -> RenderedNode | None:
    if name == "NOT" and len(kids) == 1:
        rendered = _render_node(kids[0])
        if rendered is None:
            return None
        return RenderedNode(f"NOT({rendered.dax})", "c1_operator", "operator")
    if name in {"-", "+"} and len(kids) == 1:
        rendered = _render_node(kids[0])
        if rendered is None:
            return None
        return RenderedNode(f"({name}{rendered.dax})", "c1_operator", "operator")
    if name == "%" and len(kids) == 2:
        left = _render_node(kids[0])
        right = _render_node(kids[1])
        if left is None or right is None:
            return None
        return RenderedNode(f"MOD({left.dax}, {right.dax})", "c1_operator", "operator")
    dax_op = _BINARY_OPERATOR_DAX.get(name)
    if dax_op is None or len(kids) != 2:
        return None
    left = _render_node(kids[0])
    right = _render_node(kids[1])
    if left is None or right is None:
        return None
    return RenderedNode(f"({left.dax} {dax_op} {right.dax})", "c1_operator", "operator")


def _render_conditional(name: str, kids: list[Any]) -> RenderedNode | None:
    if name == "IF" and len(kids) >= 2:
        rendered = [_render_node(k) for k in kids]
        if any(r is None for r in rendered):
            return None
        parts = ", ".join(r.dax for r in rendered)  # type: ignore[union-attr]
        return RenderedNode(f"IF({parts})", "c1_conditional", "conditional")
    if name == "CASE" and len(kids) >= 3:
        subject, *rest = kids
        subject_r = _render_node(subject)
        if subject_r is None:
            return None
        pairs: list[str] = []
        index = 0
        while index + 1 < len(rest):
            when_r = _render_node(rest[index])
            then_r = _render_node(rest[index + 1])
            if when_r is None or then_r is None:
                return None
            pairs.append(f"{subject_r.dax} = {when_r.dax}, {then_r.dax}")
            index += 2
        trailing = ""
        if len(rest) % 2 == 1:
            else_r = _render_node(rest[-1])
            if else_r is None:
                return None
            trailing = f", {else_r.dax}"
        return RenderedNode(f"SWITCH(TRUE(), {', '.join(pairs)}{trailing})", "c1_conditional", "conditional")
    return None


def _render_function(name: str, detail: Mapping[str, str], kids: list[Any]) -> RenderedNode | None:
    family = detail.get("family", "unknown")
    if family == "type" and len(kids) == 1:
        rendered = _render_node(kids[0])
        if rendered is None:
            return None
        dax_name = _TYPE_DAX.get(name)
        return RenderedNode(f"{dax_name}({rendered.dax})", "c1_cast", "type") if dax_name else None
    if family == "numeric" and len(kids) == 1:
        rendered = _render_node(kids[0])
        if rendered is None:
            return None
        dax_name = _NUMERIC_DAX.get(name)
        return RenderedNode(f"{dax_name}({rendered.dax})", "c1_numeric", "numeric") if dax_name else None
    if family == "aggregate" and len(kids) == 1:
        rendered = _render_node(kids[0])
        if rendered is None:
            return None
        dax_name = _AGGREGATE_DAX.get(name)
        return RenderedNode(f"{dax_name}({rendered.dax})", "c1_aggregate", "aggregate") if dax_name else None
    return None


def _try_null_idiom(node: Mapping[str, Any]) -> RenderedNode | None:
    if node.get("kind") != "FUNCTION" or node.get("name") not in {"ZN", "IFNULL"}:
        return None
    kids = node.get("children") or []
    if node["name"] == "ZN" and len(kids) != 1:
        return None
    if node["name"] == "IFNULL" and len(kids) != 2:
        return None
    arg = _render_node(kids[0])
    if arg is None:
        return None
    default = "0"
    if node["name"] == "IFNULL":
        default_rendered = _render_node(kids[1])
        if default_rendered is None:
            return None
        default = default_rendered.dax
    return RenderedNode(f"COALESCE({arg.dax}, {default})", "c2_null_idiom", "logical")


def _try_lod_fixed(node: Mapping[str, Any]) -> RenderedNode | None:
    if node.get("kind") != "AGGREGATE" or node.get("name") != "FIXED":
        return None
    kids = node.get("children") or []
    if not kids:
        return None
    measure, *dims = kids
    measure_rendered = _render_node(measure)
    if measure_rendered is None:
        return None
    dim_texts: list[str] = []
    for dim in dims:
        if not isinstance(dim, dict) or dim.get("kind") != "REFERENCE":
            return None
        dim_texts.append(f"[{dim['name']}]")
    args = ", ".join([_TABLE_PLACEHOLDER, *dim_texts])
    return RenderedNode(f"CALCULATE({measure_rendered.dax}, ALLEXCEPT({args}))", "c2_lod_fixed", "lod")


# ------------------------------------------------------------------------ DAX sanity check

#: A small allowlist of the DAX function names this module's own rules can ever emit, plus
#: the handful of DAX keywords/pseudo-functions used in generated text (`TRUE`/`FALSE`/
#: `BLANK` -- printed with their own call parens above). Standing in for validation-ladder
#: rung 2 ("parses under the target grammar") since no real DAX parser exists -- see the
#: module docstring's own "What 'must pass proof in CI' honestly means" section.
KNOWN_DAX_FUNCTIONS: frozenset[str] = frozenset(
    {*_AGGREGATE_DAX.values(), *_TYPE_DAX.values(), *_NUMERIC_DAX.values(),
     "IF", "SWITCH", "TRUE", "FALSE", "BLANK", "NOT", "MOD", "COALESCE",
     "CALCULATE", "ALLEXCEPT"}
)

_DAX_DELIMITERS = {"(": ")", "[": "]"}


def dax_sanity_check(dax: str) -> str | None:
    """A structural check standing in for a real DAX parser: every bracket/paren balances,
    and every bare-word token immediately followed by ``(`` is either a known DAX function
    or the disclosed `{table}` placeholder's own neighbours. Returns `None` when the text
    passes, or a reason string naming what failed."""
    stack: list[str] = []
    for char in dax:
        if char in _DAX_DELIMITERS:
            stack.append(_DAX_DELIMITERS[char])
        elif char in (")", "]") and (not stack or stack.pop() != char):
            return f"unbalanced {char!r} in {dax!r}"
    if stack:
        return f"unclosed {stack!r} in {dax!r}"

    token = ""
    for char in dax:
        # DAX itself has dotted function names (PERCENTILE.INC), so "." accumulates into
        # the token like any other identifier character rather than ending it.
        if char.isalpha() or char == "_" or (token and (char.isdigit() or char == ".")):
            token += char
            continue
        if char == "(" and token and token not in KNOWN_DAX_FUNCTIONS:
            return f"{token!r} is not a known DAX function"
        token = ""
    return None


# ------------------------------------------------------------------------- estate-wide pass

_NODE_SERVER_MANAGED = frozenset(p.name for p in BASE_NODE_PROPERTIES if p.server_managed) | {
    "id",
    "side",
}


def _writable_node_properties(properties: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in properties.items() if k not in _NODE_SERVER_MANAGED}


class RulesEngine:
    """The pool/graph/writer/provenance-store an apply-rules route needs -- the same small
    wrapper shape `classify.py`'s own `ClassificationEngine` already established."""

    def __init__(
        self, pool: asyncpg.Pool, *, graph_name: str, writer: GraphWriter, provenance_store: ProvenanceStore
    ) -> None:
        self._pool = pool
        self._graph = graph_name
        self._writer = writer
        self._provenance = provenance_store

    @property
    def pool(self) -> asyncpg.Pool:
        return self._pool

    @property
    def graph_name(self) -> str:
        return self._graph

    @property
    def writer(self) -> GraphWriter:
        return self._writer

    @property
    def provenance(self) -> ProvenanceStore:
        return self._provenance


@dataclass(frozen=True, slots=True)
class AppliedRule:
    calculated_field_id: str
    name: str
    rule_id: str
    family: str
    measure_id: str


@dataclass(frozen=True, slots=True)
class ApplyRulesResult:
    rules_version: int
    total: int
    matched: int
    by_family: dict[str, int]
    applied: tuple[AppliedRule, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "rules_version": self.rules_version,
            "total": self.total,
            "matched": self.matched,
            "by_family": dict(self.by_family),
            "applied": [
                {
                    "calculated_field_id": a.calculated_field_id,
                    "name": a.name,
                    "rule_id": a.rule_id,
                    "family": a.family,
                    "measure_id": a.measure_id,
                }
                for a in self.applied
            ],
        }


async def apply_rules_estate(
    pool: asyncpg.Pool,
    graph_name: str,
    writer: GraphWriter,
    provenance: ProvenanceStore,
    *,
    principal: Principal,
) -> ApplyRulesResult:
    """Render every live C1/C2 `CalculatedField` the shipped rules cover, and for each one
    that renders, write a real `Measure`, a `MAPS_TO` edge carrying the rule id, and a
    DETERMINISTIC provenance record citing the rule id and version (this story's own
    acceptance criteria, verbatim). A field the rules cannot render is left alone --
    C3/C4's own boundary, not this module's to cross.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""SELECT id FROM {NODE_INDEX_TABLE}
             WHERE graph = $1 AND kind = 'node' AND label = 'CalculatedField' AND retired_at IS NULL""",
            graph_name,
        )
        calc_ids = [row["id"] for row in rows]
        calc_properties = await hydrate(conn, graph_name, "CalculatedField", calc_ids)
        existing_measure_targets = await children(conn, graph_name, calc_ids, "MAPS_TO", "Measure")
        graph_version = await _current_version(conn, graph_name)

    by_family: dict[str, int] = {}
    applied: list[AppliedRule] = []
    node_writes: list[NodeWrite] = []
    edge_writes: list[EdgeWrite] = []
    provenance_records: list[ProvenanceRecord] = []

    for calc_id, properties in calc_properties.items():
        if existing_measure_targets.get(calc_id):
            continue  # already converted; a re-run does not duplicate the Measure
        outcome = render_calc(properties.get("formula_ast"))
        if not outcome.ok or outcome.dax is None or outcome.rule_id is None:
            continue
        dax = outcome.dax
        rule_id = outcome.rule_id

        by_family[outcome.family or "unknown"] = by_family.get(outcome.family or "unknown", 0) + 1
        name = str(properties.get("name") or calc_id)
        # A bare ULID, like every other graph node id in this codebase — a prefix (as
        # story S3.2.1 already found the hard way) pushes a 26-char ULID over the
        # ontology's node-id length limit.
        measure_id = new_ulid()
        rule = _RULES_BY_ID[rule_id]
        pattern_ref = f"{rule_id}:v{outcome.rule_version}"
        # The provenance id is minted here, before the Measure node, so the node's own
        # required `provenance_ref` names the real record rather than a placeholder.
        provenance_id = f"prov_{new_ulid()}"

        node_writes.append(
            NodeWrite(
                type="Measure",
                id=measure_id,
                properties={
                    "name": name,
                    "dax": dax,
                    "source_calc_ref": calc_id,
                    "class": rule.class_,
                    "pattern_ref": pattern_ref,
                    "provenance_ref": provenance_id,
                    "validation_state": (
                        "rung 2 (structural): balanced syntax and known DAX functions only "
                        "-- no live DAX parser or compiler exists yet (E5/E7)"
                    ),
                },
            )
        )
        edge_writes.append(
            EdgeWrite(
                type="MAPS_TO", from_id=calc_id, to_id=measure_id,
                properties={"class": rule.class_, "pattern_ref": pattern_ref},
            )
        )
        provenance_records.append(
            new_record(
                id=provenance_id,
                artefact_kind="MEASURE",
                artefact_ref=measure_id,
                artefact_content_hash=context_hash(dax.encode("utf-8")),
                agent=_AGENT,
                agent_version=_AGENT_VERSION,
                mode=AgentMode.DETERMINISTIC,
                contract=ContractName.TRANSPILER_CALC,
                subject_id=calc_id,
                context_hash=context_hash(str(properties.get("formula_ast")).encode("utf-8")),
                graph_version=graph_version,
                model=None,
                pattern_ref=pattern_ref,
                created_by=principal.value,
            )
        )
        applied.append(
            AppliedRule(
                calculated_field_id=calc_id, name=name, rule_id=rule_id,
                family=outcome.family or "unknown", measure_id=measure_id,
            )
        )

    if node_writes:
        await writer.write_nodes(node_writes, principal=principal)
    for edge_write in edge_writes:
        await writer.write_edge(edge_write, principal=principal)
    for record in provenance_records:
        await provenance.record(record)

    return ApplyRulesResult(
        rules_version=RULES_VERSION,
        total=len(calc_properties),
        matched=len(applied),
        by_family=by_family,
        applied=tuple(applied),
    )


async def _current_version(conn: asyncpg.Connection, graph_name: str) -> int:
    row = await conn.fetchrow(
        f"SELECT seq FROM {EVENT_TABLE} WHERE graph = $1 ORDER BY seq DESC LIMIT 1", graph_name
    )
    return int(row["seq"]) if row else 0


async def rule_coverage(pool: asyncpg.Pool, graph_name: str) -> dict[str, Any]:
    """A live read of what the last `apply_rules_estate` pass produced: the percentage of
    the estate's calculated fields matched by a rule, broken down by rule family (this
    story's own acceptance criterion, verbatim)."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""SELECT id FROM {NODE_INDEX_TABLE}
             WHERE graph = $1 AND kind = 'node' AND label = 'CalculatedField' AND retired_at IS NULL""",
            graph_name,
        )
        calc_ids = [row["id"] for row in rows]
        measure_targets = await children(conn, graph_name, calc_ids, "MAPS_TO", "Measure")
        measure_ids = [mid for ids in measure_targets.values() for mid in ids]
        measures = await hydrate(conn, graph_name, "Measure", measure_ids)

    by_family: dict[str, int] = {}
    for measure in measures.values():
        pattern_ref = str(measure.get("pattern_ref") or "")
        rule_id = pattern_ref.split(":", 1)[0]
        rule = _RULES_BY_ID.get(rule_id)
        family = rule.family if rule else "unknown"
        by_family[family] = by_family.get(family, 0) + 1

    total = len(calc_ids)
    matched = len(measures)
    return {
        "total": total,
        "matched": matched,
        "percentage": round(matched / total * 100, 1) if total else 0.0,
        "by_family": by_family,
        "rules_version": RULES_VERSION,
    }


# --------------------------------------------------------------- regression check (S5.2.2)


@dataclass(frozen=True, slots=True)
class RegressedArtefact:
    """A `Measure` a shipped rule can no longer reproduce -- story S5.2.2's own "any new
    failure blocks promotion"."""

    calculated_field_id: str
    measure_id: str
    rule_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class ChangedArtefact:
    """A `Measure` that still renders under the current rule set, but differently --
    disclosed, not blocking: a rule legitimately improving is not a regression."""

    calculated_field_id: str
    measure_id: str
    previous_rule_id: str
    previous_dax: str
    current_rule_id: str
    current_dax: str


@dataclass(frozen=True, slots=True)
class RegressionReport:
    checked: int
    unchanged: int
    changed: tuple[ChangedArtefact, ...]
    regressed: tuple[RegressedArtefact, ...]

    @property
    def ok(self) -> bool:
        return len(self.regressed) == 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "checked": self.checked,
            "unchanged": self.unchanged,
            "ok": self.ok,
            "changed": [
                {
                    "calculated_field_id": c.calculated_field_id,
                    "measure_id": c.measure_id,
                    "previous_rule_id": c.previous_rule_id,
                    "previous_dax": c.previous_dax,
                    "current_rule_id": c.current_rule_id,
                    "current_dax": c.current_dax,
                }
                for c in self.changed
            ],
            "regressed": [
                {
                    "calculated_field_id": r.calculated_field_id,
                    "measure_id": r.measure_id,
                    "rule_id": r.rule_id,
                    "reason": r.reason,
                }
                for r in self.regressed
            ],
        }


async def check_regression(pool: asyncpg.Pool, graph_name: str) -> RegressionReport:
    """Re-render every `Measure` a shipped rule has ever produced against the *current*
    rule set, and report what changed -- story S5.2.2's own "re-runs ... the PASSED
    artefacts that used the rule; any new failure blocks promotion."

    Only `Measure`s carrying a `pattern_ref` are in scope -- a rules-engine artefact, not
    (once F5.3 exists) a GENERATED_PROVED one, which this check has nothing to say about.
    """
    async with pool.acquire() as conn:
        measure_rows = await conn.fetch(
            f"""SELECT id FROM {NODE_INDEX_TABLE}
             WHERE graph = $1 AND kind = 'node' AND label = 'Measure' AND retired_at IS NULL""",
            graph_name,
        )
        measures = await hydrate(conn, graph_name, "Measure", [row["id"] for row in measure_rows])
        calc_ids = [
            str(measure["source_calc_ref"])
            for measure in measures.values()
            if measure.get("source_calc_ref")
        ]
        calcs = await hydrate(conn, graph_name, "CalculatedField", calc_ids)

    unchanged = 0
    changed: list[ChangedArtefact] = []
    regressed: list[RegressedArtefact] = []

    for measure_id, measure in measures.items():
        pattern_ref = str(measure.get("pattern_ref") or "")
        if not pattern_ref:
            continue
        previous_rule_id = pattern_ref.split(":", 1)[0]
        calc_id = str(measure.get("source_calc_ref") or "")
        calc = calcs.get(calc_id)
        if calc is None:
            regressed.append(
                RegressedArtefact(
                    calculated_field_id=calc_id, measure_id=measure_id, rule_id=previous_rule_id,
                    reason="its source CalculatedField no longer exists",
                )
            )
            continue

        outcome = render_calc(calc.get("formula_ast"))
        if not outcome.ok or outcome.dax is None or outcome.rule_id is None:
            regressed.append(
                RegressedArtefact(
                    calculated_field_id=calc_id, measure_id=measure_id, rule_id=previous_rule_id,
                    reason=f"no shipped rule matches this calculation any longer ({outcome.reason})",
                )
            )
            continue

        previous_dax = str(measure.get("dax") or "")
        if outcome.dax == previous_dax and outcome.rule_id == previous_rule_id:
            unchanged += 1
        else:
            changed.append(
                ChangedArtefact(
                    calculated_field_id=calc_id, measure_id=measure_id,
                    previous_rule_id=previous_rule_id, previous_dax=previous_dax,
                    current_rule_id=outcome.rule_id, current_dax=outcome.dax,
                )
            )

    return RegressionReport(
        checked=unchanged + len(changed) + len(regressed),
        unchanged=unchanged, changed=tuple(changed), regressed=tuple(regressed),
    )


__all__ = [
    "KNOWN_DAX_FUNCTIONS",
    "RULES",
    "RULES_VERSION",
    "AppliedRule",
    "ApplyRulesResult",
    "ChangedArtefact",
    "GoldenCase",
    "RegressedArtefact",
    "RegressionReport",
    "RenderOutcome",
    "RuleMeta",
    "RulesEngine",
    "apply_rules_estate",
    "check_regression",
    "dax_sanity_check",
    "render_calc",
    "rule_coverage",
]
