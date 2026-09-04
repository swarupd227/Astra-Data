"""The Tableau function set, by family — specification Appendix B.1, story S2.3.1.

    "Grammar covers the Tableau function set in Appendix B of the spec, LOD expressions
    (FIXED / INCLUDE / EXCLUDE), table calculations with addressing and partitioning,
    parameters, type conversions, string, date, logical and aggregation functions"

**Why a registry and not grammar rules.** The grammar accepts any `NAME(...)`; this decides
whether the platform *knows* it. The split matters: a grammar that enumerated function names
would reject a valid calculation the moment Tableau shipped a function, turning "we do not
recognise this" — a parse-quality finding an engineer can work down — into "this workbook will
not parse", which is a harvest failure they cannot. S2.3.1's third criterion asks for the
first behaviour explicitly: *captured verbatim and flagged, never dropped*.

**Families are Appendix B.1's, not ours.** The table maps each family to a DAX target and a
default class, and §9's classification keys off exactly that. Recording the family on the AST
node is what lets the Transpiler ask "is this C1?" without re-deriving it from a function
name, and what lets the Pattern Library match on shape.

Appendix B.1 gives *examples* rather than an exhaustive list, so this registry is wider than
the table and narrower than Tableau. Where it is narrower, the construct is flagged and kept —
which is the whole point.
"""

from __future__ import annotations

from enum import Enum


class Family(str, Enum):
    """Appendix B.1's families, verbatim."""

    AGGREGATE = "aggregate"
    LOGICAL = "logical"
    STRING = "string"
    DATE = "date"
    TYPE = "type"
    LOD = "lod"
    TABLE_CALC_SIMPLE = "table_calc_simple"
    TABLE_CALC_COMPLEX = "table_calc_complex"
    PARAMETER = "parameter"
    SET = "set"
    RAWSQL = "rawsql"
    ATTR = "attr"
    NUMERIC = "numeric"
    """Not a family of its own in B.1 — ABS, ROUND, FLOOR and their kin sit under
    "Arithmetic / logical" alongside the operators. Separated here because the operators
    classify as C1 unconditionally and the numeric *functions* do not all have a DAX
    equivalent, and merging them would hide that distinction from the Transpiler."""

    USER = "user"
    """USERNAME, ISMEMBEROF, FULLNAME. Not in B.1's table, and deliberately named: S2.3.2
    detects row-level security from exactly these, and a workbook using them has an access
    model the target has to reproduce."""

    UNKNOWN = "unknown"


#: Appendix B.1, "Aggregate".
# fmt: off
AGGREGATE = frozenset({
    "SUM", "AVG", "MIN", "MAX", "COUNT", "COUNTD", "MEDIAN", "PERCENTILE", "STDEV", "STDEVP",
    "VAR", "VARP", "CORR", "COVAR", "COVARP", "COLLECT"
})
# fmt: on

#: "Arithmetic / logical" — the function half; the operators are grammar rules.
LOGICAL = frozenset({"IIF", "IFNULL", "ISNULL", "ZN", "IFTHENELSE", "AND", "OR", "NOT", "ISDATE"})

# fmt: off
NUMERIC = frozenset({
    "ABS", "CEILING", "FLOOR", "ROUND", "SIGN", "SQRT", "SQUARE", "POWER", "EXP", "LN", "LOG",
    "PI", "DEGREES", "RADIANS", "SIN", "COS", "TAN", "ASIN", "ACOS", "ATAN", "ATAN2", "COT",
    "DIV", "HEXBINX", "HEXBINY", "MIN", "MAX"
})
# fmt: on

#: Appendix B.1, "String". REGEXP_* is included and classified — B.1 maps it to "M or C4",
#: which is a decision the Transpiler makes and not a reason to leave it unrecognised.
# fmt: off
STRING = frozenset({
    "LEFT", "RIGHT", "MID", "LEN", "CONTAINS", "REPLACE", "SPLIT", "TRIM", "LTRIM", "RTRIM",
    "UPPER", "LOWER", "STARTSWITH", "ENDSWITH", "FIND", "FINDNTH", "ASCII", "CHAR", "SPACE",
    "PROPER", "REGEXP_MATCH", "REGEXP_EXTRACT", "REGEXP_EXTRACT_NTH", "REGEXP_REPLACE"
})
# fmt: on

#: Appendix B.1, "Date".
# fmt: off
DATE = frozenset({
    "DATEPART", "DATETRUNC", "DATEADD", "DATEDIFF", "DATENAME", "TODAY", "NOW", "MAKEDATE",
    "MAKEDATETIME", "MAKETIME", "YEAR", "QUARTER", "MONTH", "WEEK", "DAY", "HOUR", "MINUTE",
    "SECOND", "ISOYEAR", "ISOQUARTER", "ISOWEEK", "ISOWEEKDAY", "DATEPARSE"
})
# fmt: on

#: Appendix B.1, "Type".
TYPE = frozenset({"INT", "FLOAT", "STR", "DATE", "DATETIME", "BOOL", "CAST"})

#: Appendix B.1, "Table calc — simple": addressing resolves from the sheet.
# fmt: off
TABLE_CALC_SIMPLE = frozenset({
    "RUNNING_SUM", "RUNNING_AVG", "RUNNING_MIN", "RUNNING_MAX", "RUNNING_COUNT", "TOTAL",
    "WINDOW_SUM", "WINDOW_AVG", "WINDOW_MIN", "WINDOW_MAX", "WINDOW_COUNT", "WINDOW_MEDIAN",
    "WINDOW_STDEV", "WINDOW_STDEVP", "WINDOW_VAR", "WINDOW_VARP", "WINDOW_PERCENTILE",
    "WINDOW_CORR", "WINDOW_COVAR"
})
# fmt: on

#: "Table calc — complex": B.1 marks these C3/C4 because the grain is not fixed by the sheet.
# fmt: off
TABLE_CALC_COMPLEX = frozenset({
    "LOOKUP", "PREVIOUS_VALUE", "INDEX", "FIRST", "LAST", "SIZE", "RANK", "RANK_DENSE",
    "RANK_MODIFIED", "RANK_PERCENTILE", "RANK_UNIQUE", "SCRIPT_BOOL", "SCRIPT_INT",
    "SCRIPT_REAL", "SCRIPT_STR"
})
# fmt: on

#: Appendix B.1, "RAWSQL" — "C4 by default".
# fmt: off
RAWSQL = frozenset({
    "RAWSQL_BOOL", "RAWSQL_DATE", "RAWSQL_DATETIME", "RAWSQL_INT", "RAWSQL_REAL", "RAWSQL_STR",
    "RAWSQLAGG_BOOL", "RAWSQLAGG_DATE", "RAWSQLAGG_DATETIME", "RAWSQLAGG_INT", "RAWSQLAGG_REAL",
    "RAWSQLAGG_STR"
})
# fmt: on

#: Appendix B.1, "Sets / groups / bins".
SET = frozenset({"IN", "BIN", "GROUP"})

USER = frozenset({"USERNAME", "FULLNAME", "ISMEMBEROF", "ISUSERNAME", "ISFULLNAME", "USERDOMAIN"})

ATTR = frozenset({"ATTR"})

_BY_FAMILY: tuple[tuple[Family, frozenset[str]], ...] = (
    # Ordered so a name in two sets lands in the family that decides its class. RAWSQL and
    # the table calcs are checked before the aggregates because SCRIPT_* and WINDOW_* would
    # otherwise be read as ordinary functions and classified C1 — which would send an
    # untranslatable construct through the Transpiler as if it were a SUM.
    (Family.RAWSQL, RAWSQL),
    (Family.TABLE_CALC_COMPLEX, TABLE_CALC_COMPLEX),
    (Family.TABLE_CALC_SIMPLE, TABLE_CALC_SIMPLE),
    (Family.ATTR, ATTR),
    (Family.USER, USER),
    (Family.AGGREGATE, AGGREGATE),
    (Family.TYPE, TYPE),
    (Family.DATE, DATE),
    (Family.STRING, STRING),
    (Family.LOGICAL, LOGICAL),
    (Family.SET, SET),
    (Family.NUMERIC, NUMERIC),
)

#: Every function this grammar version recognises. What the conformance suite's AST-coverage
#: check measures the golden corpus against.
KNOWN_FUNCTIONS: frozenset[str] = frozenset().union(*(names for _, names in _BY_FAMILY))


def family_of(name: str) -> Family:
    """Which Appendix B.1 family a function belongs to, or `UNKNOWN`.

    `UNKNOWN` is a real answer and not an error: §6.2 requires the construct to be retained
    verbatim and parse quality lowered, and the Parse Quality Queue is where an engineer
    decides what to do about it.
    """
    upper = name.upper()
    for family, names in _BY_FAMILY:
        if upper in names:
            return family
    return Family.UNKNOWN


def is_known(name: str) -> bool:
    return name.upper() in KNOWN_FUNCTIONS


def is_table_calc(name: str) -> bool:
    """Whether this call is a table calculation.

    Worth its own function because §4.1.1 puts `table_calc_flag` on CalculatedField, and
    because a table calculation's addressing and partitioning come from the *sheet* (§6.2) —
    so an AST containing one is incomplete until the sheet is read, and the node says so.
    """
    return family_of(name) in {Family.TABLE_CALC_SIMPLE, Family.TABLE_CALC_COMPLEX}
