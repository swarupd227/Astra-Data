"""Guarding the read-only Cypher endpoint.

S1.1.2 asks for "a read-only Cypher endpoint ... with a 30-second timeout and a
10,000-row cap". The endpoint hands a caller-supplied string to Apache AGE, so the guard
has three jobs and they are layered deliberately — the lexical checks here exist to give a
usable error message, and the read-only transaction in the repository is what actually
makes the promise true.

1. **Containment.** The query is interpolated into a dollar-quoted SQL string —
   ``cypher('graph', $$ ... $$, params)`` — so a query containing a dollar-quote
   delimiter or a statement separator could escape into SQL. Those are rejected outright;
   there is no legitimate Cypher that needs them.
2. **Read-only intent.** Write clauses are rejected by name, so the caller is told what
   they did rather than getting a transaction error.
3. **Shape.** AGE requires the returned columns to be declared in SQL, which means the
   RETURN clause has to be understood before the query runs.

The scanner strips string literals and comments before looking for keywords, so a
workbook called "Create new report" in a WHERE clause is not mistaken for a write.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Cypher clauses that write. AGE supports all of these.
WRITE_KEYWORDS = frozenset(
    {"CREATE", "MERGE", "SET", "DELETE", "DETACH", "REMOVE", "DROP", "LOAD"}
)

#: Clauses that terminate a RETURN item list.
_RETURN_TERMINATORS = ("ORDER", "SKIP", "LIMIT", "UNION")

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

#: Any dollar-quote delimiter: `$$`, `$tag$`. Rejected, since the query is embedded in one.
_DOLLAR_QUOTE = re.compile(r"\$[A-Za-z_0-9]*\$")

MAX_QUERY_LENGTH = 20_000


class CypherRejected(Exception):
    """The query is not accepted by the read-only endpoint."""

    def __init__(self, reason: str, *, code: str) -> None:
        self.code = code
        super().__init__(reason)


@dataclass(frozen=True, slots=True)
class AcceptedQuery:
    text: str
    columns: tuple[str, ...]

    @property
    def column_definition(self) -> str:
        """The SQL column definition list AGE requires after ``AS``."""
        return ", ".join(f'"{name}" ag_catalog.agtype' for name in self.columns)


def strip_literals_and_comments(query: str) -> str:
    """Replace string literals and comments with spaces, preserving length and structure.

    Keyword and bracket scanning runs over the result, so a keyword inside a string
    literal cannot trigger a rejection and a bracket inside one cannot unbalance the
    depth count.
    """
    out: list[str] = []
    index, length = 0, len(query)
    while index < length:
        char = query[index]
        if char in ("'", '"', "`"):
            quote = char
            out.append(" ")
            index += 1
            while index < length:
                if query[index] == "\\" and index + 1 < length:
                    out.append("  ")
                    index += 2
                    continue
                if query[index] == quote:
                    out.append(" ")
                    index += 1
                    break
                out.append(" " if query[index] != "\n" else "\n")
                index += 1
            continue
        if char == "/" and index + 1 < length and query[index + 1] == "/":
            while index < length and query[index] != "\n":
                out.append(" ")
                index += 1
            continue
        if char == "/" and index + 1 < length and query[index + 1] == "*":
            while index < length and not (query[index] == "*" and index + 1 < length
                                          and query[index + 1] == "/"):
                out.append(" " if query[index] != "\n" else "\n")
                index += 1
            out.append("  ")
            index += 2
            continue
        out.append(char)
        index += 1
    return "".join(out)


def _split_top_level(text: str, separator: str = ",") -> list[str]:
    """Split on ``separator`` at bracket depth zero."""
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for char in text:
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        if char == separator and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(char)
    parts.append("".join(current))
    return [part.strip() for part in parts if part.strip()]


def _find_last_top_level_return(scrubbed: str) -> int:
    """Index of the last ``RETURN`` at bracket depth zero, or -1."""
    depth = 0
    found = -1
    for match in re.finditer(r"[([{}\])]|\bRETURN\b", scrubbed, flags=re.IGNORECASE):
        token = match.group(0)
        if token in "([{":
            depth += 1
        elif token in ")]}":
            depth -= 1
        elif depth == 0:
            found = match.start()
    return found


def derive_columns(query: str) -> tuple[str, ...]:
    """Column names implied by the query's final RETURN clause.

    AGE needs the result columns declared in SQL, so a returned item must be a bare
    identifier or carry an alias. ``RETURN *`` cannot be resolved without knowing the
    query's bound variables and is refused with an explanation rather than guessed at.
    """
    scrubbed = strip_literals_and_comments(query)
    start = _find_last_top_level_return(scrubbed)
    if start < 0:
        raise CypherRejected(
            "the query must end in a RETURN clause; this endpoint reads and returns rows",
            code="no_return_clause",
        )

    tail = scrubbed[start + len("RETURN"):]
    original_tail = query[start + len("RETURN"):]

    # Trim at the first top-level ORDER / SKIP / LIMIT / UNION.
    cut = len(tail)
    depth = 0
    for match in re.finditer(r"[([{}\])]|\b[A-Za-z]+\b", tail):
        token = match.group(0)
        if token in "([{":
            depth += 1
        elif token in ")]}":
            depth -= 1
        elif depth == 0 and token.upper() in _RETURN_TERMINATORS:
            cut = match.start()
            break
    items_scrubbed = tail[:cut]
    items_original = original_tail[:cut]

    if items_scrubbed.strip().startswith("*"):
        raise CypherRejected(
            "RETURN * is not supported: the endpoint must declare the result columns to "
            "Apache AGE before the query runs. Name each returned item, for example "
            "'RETURN w AS workbook'.",
            code="return_star",
        )

    # Split the original text using boundaries computed from the scrubbed text, so an
    # alias is read verbatim but a comma inside a string literal does not split an item.
    boundaries = [0]
    depth = 0
    for position, char in enumerate(items_scrubbed):
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == "," and depth == 0:
            boundaries.append(position + 1)
    boundaries.append(len(items_scrubbed) + 1)

    columns: list[str] = []
    for index in range(len(boundaries) - 1):
        raw = items_original[boundaries[index]: boundaries[index + 1] - 1].strip()
        if not raw:
            continue
        alias_match = re.search(r"\bAS\s+([A-Za-z_][A-Za-z0-9_]*)\s*$", raw, flags=re.IGNORECASE)
        if alias_match:
            columns.append(alias_match.group(1))
            continue
        if _IDENTIFIER.match(raw):
            columns.append(raw)
            continue
        raise CypherRejected(
            f"returned item {raw!r} needs an alias so the result column can be named, "
            f"for example '{raw} AS value'",
            code="unaliased_return_item",
        )

    if not columns:
        raise CypherRejected("the RETURN clause returns nothing", code="empty_return")
    duplicates = {name for name in columns if columns.count(name) > 1}
    if duplicates:
        raise CypherRejected(
            f"result columns must be unique; {', '.join(sorted(duplicates))} appears twice",
            code="duplicate_return_columns",
        )
    return tuple(columns)


def accept(query: str, *, columns: list[str] | None = None) -> AcceptedQuery:
    """Validate a caller's Cypher and resolve its result columns.

    ``columns`` overrides the derived names, for the queries whose RETURN clause the
    deriver cannot read.
    """
    if not query or not query.strip():
        raise CypherRejected("the query is empty", code="empty_query")
    if len(query) > MAX_QUERY_LENGTH:
        raise CypherRejected(
            f"the query exceeds {MAX_QUERY_LENGTH} characters", code="query_too_long"
        )

    if _DOLLAR_QUOTE.search(query):
        raise CypherRejected(
            "the query may not contain a dollar-quote delimiter ($$ or $tag$)",
            code="dollar_quote",
        )

    scrubbed = strip_literals_and_comments(query)

    if ";" in scrubbed:
        raise CypherRejected(
            "the query may not contain ';'; this endpoint runs exactly one statement",
            code="statement_separator",
        )

    found_writes = sorted(
        {
            match.group(0).upper()
            for match in re.finditer(r"\b[A-Za-z_]+\b", scrubbed)
            if match.group(0).upper() in WRITE_KEYWORDS
        }
    )
    if found_writes:
        raise CypherRejected(
            f"this endpoint is read-only; {', '.join(found_writes)} "
            f"{'is' if len(found_writes) == 1 else 'are'} not permitted",
            code="write_clause",
        )

    resolved: tuple[str, ...]
    if columns:
        for name in columns:
            if not _IDENTIFIER.match(name):
                raise CypherRejected(
                    f"column name {name!r} is not an identifier", code="invalid_column_name"
                )
        if len(set(columns)) != len(columns):
            raise CypherRejected("column names must be unique", code="duplicate_return_columns")
        resolved = tuple(columns)
    else:
        resolved = derive_columns(query)

    return AcceptedQuery(text=query.strip(), columns=resolved)
