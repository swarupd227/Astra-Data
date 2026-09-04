"""The Transpiler's context contract, spec §4.1.3.

The specification states it in one sentence, and S1.3.1 restates it as an acceptance
criterion ending in two words that do most of the work:

    one CalculatedField, its transitive DEPENDS_ON closure, the Parameter domains it
    references, the target ModelTable columns those fields MAPS_TO, and the Pattern
    records whose source_signature matches its AST shape — **nothing else**

"Nothing else" is why the fragments are short and why there is a test that asserts the set
of keys in the assembled document. Every extra field is a token spent on every call, a
larger surface across the inference boundary, and one more thing that changes the
context hash when it changes.

**What is deliberately absent, and why:**

* ``created_by``, ``created_at``, ``created_in_run``, ``updated_at`` and the retirement
  properties. Audit metadata is about the record, not about the calculation. Including it
  would make the context hash change when a workbook is re-harvested with no semantic
  change, so the gateway's context-hash cache (§5.4) would never hit and a provenance
  record could not be checked by re-assembling.
* ``Parameter.current_values_seen``. §4.1.3 asks for "the Parameter domains it
  references"; observed values are data the client's users entered, and §18.3 puts data
  on the far side of the inference boundary. The domain, datatype and default are the
  definition, and the definition is what a transpiler needs.
* ``Field.hidden``, ``Field.default_agg``'s siblings and everything else on Field beyond
  name, datatype and role. A dependency contributes its identity and its type to the
  translation; whether it is hidden in Tableau does not change the DAX.
* ``Pattern.provenance`` and ``pass_count``. They decide whether a pattern should be
  promoted, which is the Pattern Library's question, not the Transpiler's. The Transpiler
  needs the template and the class.
* ``ModelTable.source_table_refs``. The contract carries the *columns* the source fields
  map to, which is what §4.1.3 asks for; the table's own provenance is not part of
  translating a calculation. ``mode`` stays, because DirectQuery restricts which DAX a
  measure may use and a transpiler that cannot see the mode will write invalid measures.

**Why the columns are two sections rather than one.** A column is identified by a table
and a name, and the name is on the MAPS_TO edge while the table is a node. Flattening the
table's name into each column entry would put the same string in the context once per
column and make the document say the same thing twice — two facts that could disagree.
So ``model_tables`` carries the tables and ``model_columns`` carries the mapping, joined
by id, and each comes from a fragment the schema validated.
"""

from __future__ import annotations

from .contract import Budget, ContextContract, ContractName, SectionSpec

#: Deep enough for any calculation chain a person wrote, and a bound rather than a hope:
#: DEPENDS_ON is acyclic by construction, but a defective parser could make it otherwise.
CLOSURE_DEPTH = 12

FRAGMENTS = """
fragment TranspilerSubject on CalculatedField {
  id
  name
  formula
  formula_ast
  class
  lod_type
  table_calc_flag
}

fragment TranspilerDependencyField on Field {
  id
  name
  datatype
  role
  default_agg
}

fragment TranspilerDependencyCalc on CalculatedField {
  id
  name
  formula
  formula_ast
  lod_type
  table_calc_flag
}

fragment TranspilerParameter on Parameter {
  id
  name
  datatype
  domain
  default
}

fragment TranspilerModelTable on ModelTable {
  id
  name
  mode
}

fragment TranspilerModelColumn on MAPS_TO {
  from_id
  to_id
  target_column
}

fragment TranspilerPattern on Pattern {
  id
  name
  class
  source_signature
  target_template
  promotion_state
}
"""

TRANSPILER_CALC = ContextContract(
    name=ContractName.TRANSPILER_CALC,
    version="1.0.0",
    subject_type="CalculatedField",
    description=(
        "One calculated field and everything needed to translate it: what it is built "
        "from, the parameters that bound it, where its inputs land in the target model, "
        "and the patterns whose shape it matches."
    ),
    spec_ref="§4.1.3",
    fragments=FRAGMENTS,
    sections=(
        SectionSpec(
            name="subject",
            description="The calculated field to translate.",
            fragment="TranspilerSubject",
            spec_ref="§4.1.3",
        ),
        SectionSpec(
            name="dependency_fields",
            description="Source fields in the transitive DEPENDS_ON closure.",
            fragment="TranspilerDependencyField",
            spec_ref="§4.1.3",
        ),
        SectionSpec(
            name="dependency_calculations",
            description="Other calculated fields in the transitive DEPENDS_ON closure.",
            fragment="TranspilerDependencyCalc",
            spec_ref="§4.1.3",
        ),
        SectionSpec(
            name="parameters",
            description="Parameters the closure references, with their domains.",
            fragment="TranspilerParameter",
            spec_ref="§4.1.3, §10.1",
        ),
        SectionSpec(
            name="model_tables",
            description="Target model tables the subject and its dependencies map into.",
            fragment="TranspilerModelTable",
            spec_ref="§4.1.3",
        ),
        SectionSpec(
            name="model_columns",
            description=(
                "Where each source field lands: the MAPS_TO edge's target column, keyed "
                "by source field and model table."
            ),
            fragment="TranspilerModelColumn",
            spec_ref="§4.1.2, §4.1.3",
            kind="edge",
        ),
        SectionSpec(
            name="patterns",
            description="Patterns whose source_signature matches the subject's AST shape.",
            fragment="TranspilerPattern",
            spec_ref="§4.1.3, §4.3",
        ),
    ),
    # A calculation whose context does not fit in 256 KB is not a calculation the
    # Transpiler should be attempting: it is a signal that the closure has pulled in half
    # the workbook, and an engineer should see it before a model does. 400 nodes is the
    # same judgement in the other unit — a hand-written calculation with more than four
    # hundred distinct dependencies is a data model, not a formula.
    budget=Budget(bytes=256 * 1024, nodes=400),
)

__all__ = ["CLOSURE_DEPTH", "FRAGMENTS", "TRANSPILER_CALC"]
