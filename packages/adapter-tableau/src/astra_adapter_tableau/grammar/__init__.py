"""The Tableau calculation grammar — story S2.3.1, specification Appendix B.1.

§5.4 commits the calc-language parsers to Lark, versioned with the adapter. `tableau.lark` is
the grammar, `parser.py` turns its tree into the SDK's `CalcAST`, and `functions.py` is the
Appendix B.1 function set the platform recognises.

The grammar accepts more than the registry recognises, deliberately: a construct outside the
registry is captured verbatim and flagged rather than dropped or raised on (S2.3.1's third
criterion), which is what makes the Parse Quality Queue a work list rather than a failure log.
"""

from __future__ import annotations

from .functions import KNOWN_FUNCTIONS, Family, family_of, is_known, is_table_calc
from .parser import GRAMMAR_VERSION, TableauGrammar, grammar, parse_calculation

__all__ = [
    "GRAMMAR_VERSION",
    "KNOWN_FUNCTIONS",
    "Family",
    "TableauGrammar",
    "family_of",
    "grammar",
    "is_known",
    "is_table_calc",
    "parse_calculation",
]
