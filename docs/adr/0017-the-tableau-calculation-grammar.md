# ADR 0017 — The grammar accepts more than the platform recognises

Status: accepted · 3 September 2026 · Story S2.3.1 (E2 / F2.3)

## Context

S2.3.1 asks for the Tableau calculation language parsed into a typed AST, covering Appendix
B.1's function families, with source spans, a golden corpus that parses at 100%, constructs
outside the grammar captured verbatim, and a versioned grammar recorded on every parse.

§5.4 settles the tool: *"the calc-language parsers are generated from grammars (Lark) and
versioned with the adapter."*

## Decisions

### 1. The grammar accepts any call; a registry decides what is recognised

`tableau.lark` has no list of function names. Any `NAME(...)` parses. `functions.py` holds
Appendix B.1's families and decides whether the platform *knows* the call.

This is the decision the whole story turns on. A grammar that enumerated function names would
reject a valid calculation the moment Tableau shipped one — turning "we do not recognise this"
(a parse-quality finding an engineer works down through S1.4.3's queue) into "this workbook
will not parse" (a harvest failure they cannot act on). S2.3.1's third criterion asks for the
first behaviour in as many words.

The consequence is that an unrecognised function still yields a `FUNCTION` node with its
arguments. Its structure is real: the Modeller can still see which fields it depends on,
whether or not the Transpiler can emit it. Collapsing it to an opaque blob would throw away
information that is perfectly readable.

### 2. Earley, not LALR

Tableau's language is ambiguous in places an LALR table cannot resolve without contorting the
rules — `IN` is both an operator and a set test, `MIN` is both an aggregate and a numeric
function. A grammar contorted to fit a parser generator is a grammar nobody can review against
the language it describes, and reviewing it against the language is the only way to know it is
right. Earley is slower per expression; a workbook has tens of calculations, not millions.

### 3. Canonical text is the *language*, normalised — not the classification

This was found by the conformance suite and is the subtlest thing in the story.

The first version printed derived detail into the canonical form:
`SUM{family=aggregate}([Sales])`. No source grammar can read that back, so §6.3's round-trip
could only pass for a parser taught a syntax its own language does not have.

Worse was `IF(a, b, c)`. It round-tripped **by text** while silently changing shape: a grammar
reading it back sees a function named `IF` and produces a `FUNCTION` node. The text check
passed and the AST was different — a check reporting a stability it did not have, which is the
worst outcome available.

So the canonical form is the language: `{FIXED [Desk] : SUM([Notional])}`,
`IF … THEN … ELSEIF … ELSE … END`. And `detail` is excluded, because it is derived (`family`
follows from the name) or is not part of the expression at all (a window's addressing comes
from the *sheet*, per §6.2).

### 4. Spans are carried, and excluded from shape

Every parsed node holds `(start, end)` into the source. That is S2.3.1's second criterion, and
it is the difference between a parity failure reading "the calculation is wrong" and one that
underlines the offending divide.

They are excluded from the canonical text and therefore from AST *shape*. The Pattern Library
matches on shape (§9.1) and S1.3.1 hashes it; two calculations differing only in whitespace
are the same calculation, and a span is a fact about one piece of text. `without_spans()` is
the explicit form of that distinction.

### 5. A table calculation's addressing is recorded as **unresolved**

§6.2: addressing and partitioning come from the sheet, not the expression. The node says
`unresolved` rather than carrying a default, so nothing downstream mistakes a default for a
fact. S2.3.2 reads the sheet and fills them in.

### 6. Parse quality counts constructs, not calculations

Before the grammar, a calculated field was one unread construct. Now every node inside it
counts. A workbook with one unreadable function in a fifty-node formula must not score the
same as one whose calculation is entirely unreadable — §4.1.4's threshold exists to tell them
apart, and counting per-calculation would flatten the difference.

### 7. An expression that will not parse yields one UNKNOWN node holding all of it

A client's estate contains calculations written over fifteen years by people who have left,
including ones Tableau itself renders as errors. Raising fails the harvest; returning a partial
tree loses the rest of the workbook silently. Neither is acceptable, so the whole text is kept
with its span, quality is zero for that field, and the Parse Quality Queue has something to
show somebody.

## Consequences

- **The Tableau adapter now passes conformance**: 8 passed, 0 failed, 3 skipped, and it has
  been promoted on the local tenant through S2.1.2's gate. The three skips are capabilities it
  does not claim — F2.4's execution and visual capture. **Passing means "does what it says",
  not "does everything"**: this adapter can harvest an estate and cannot yet prove parity on
  one.
- The golden corpus is 40 expressions across every Appendix B.1 family, including the awkward
  cases a real estate is full of: comments, doubled quotes, an LOD with no dimensions, a field
  named `[IFRS Rating]`. A corpus of clean expressions certifies a parser against a language
  nobody writes.
- `parse` now emits `CalculatedField` nodes with §4.1.1's required `formula_ast`, plus
  `lod_type`, `table_calc_flag`, `depends_on` and `DEPENDS_ON` edges where the target exists in
  the same fragment.
- `CalcNode.span` was added to the SDK. Additive and optional, so `INTERFACE_VERSION` does not
  move (ADR 0015's rule).

## What building it found

1. **The canonical form was unreadable by any grammar** — decision 3. Found by running the
   conformance suite rather than by reading the code, and the `IF(...)` case is the one worth
   remembering: it passed a text-equality check while changing the AST.
2. **Removing the dead branches was not tidying.** The fake source's grammar had been taught
   to read `IF(a, b, c)` and `NAME{k=v}(…)` in S2.1.1, because canonical text emitted them.
   Once it stopped, the `IF(` branch actively broke — it swallowed `IF (cond) THEN` as a
   one-argument call, and nine SDK tests went red. A branch that reads a form nothing produces
   is not inert.
3. **The registry's family order decides a classification.** `SCRIPT_REAL` and `WINDOW_SUM`
   would be read as ordinary functions and classified C1 if the aggregates were checked first
   — sending an untranslatable construct through the Transpiler as if it were a `SUM`.

## Open questions for the product owner

1. **Recognised is not translatable.** `RAWSQL_INT` is in the registry, so a workbook using it
   parses at 1.0 and is not held. Appendix B.1 classifies it C4 — a manual rewrite — and
   nothing yet surfaces "this estate contains 340 RAWSQL calls" before the Transpiler meets
   them one at a time. The AST carries the family, so the report is cheap; where it belongs is
   a programme-shaping question.
2. **The grammar has never met a real workbook.** Every expression it has parsed was written
   for this repository. The first client sample will contain constructs nobody here thought
   of, and the honest expectation is that `tableau-1` becomes `tableau-2` within a week of it
   arriving. That is the mechanism working, but it means the 100% corpus rate is a statement
   about the corpus.
3. **Parameters are indistinguishable from fields in the AST.** Tableau writes them
   identically, so the node is a `REFERENCE` and the workbook's parameter list decides. S2.3.2
   reads that list — until then, `depends_on` mixes the two and a `DEPENDS_ON` edge to a
   parameter is not written.
4. **No performance figure.** Earley on a 40-line formula has not been measured, and §8.4's
   500 workbooks per hour per site worker is the budget it has to fit inside. Worth measuring
   against a client sample rather than against a corpus written to be readable.
