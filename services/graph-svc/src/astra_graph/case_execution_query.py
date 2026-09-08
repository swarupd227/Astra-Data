"""§10.2's own query construction -- a standalone, dependency-free module.

Split out of `case_execution.py` (S7.3.1) rather than merely called from there: these
three functions are genuinely pure (stdlib-only), while `case_execution.py` itself
imports `asyncpg`/`pyarrow` at module level for the rest of dual execution. A regression
export (`regression_export.py`, S7.7.1) vendors this file verbatim into the handover
bundle so `run_suite.py` can build the identical target-side query text -- filters and
parameters converted the identical way -- standalone, without pulling in either of those
dependencies or anything database-shaped. One definition, imported by both
`case_execution.py` and the exported bundle, rather than a second copy that could drift
from it.
"""

from __future__ import annotations

from typing import Any


def to_sdk_filters(filter_ctx: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    """The case's own filter context (S7.2.1/S7.2.2) as §6.2's flat ``(field, value)``
    pairs -- "applied as the sheet applies them ... through vf_ parameters"
    (`astra_adapter.proof.ParityCase.filters`'s own docstring). A `categorical_value`
    context is exactly one pair; the default context re-states every categorical
    filter's own harvested members as repeated pairs on the same field -- the same
    repeated-parameter shape a real Tableau `vf_` call already uses for a multi-select
    filter, so no richer shape was needed here."""
    kind = filter_ctx.get("kind")
    if kind == "categorical_value":
        field_ref = filter_ctx.get("field_ref")
        value = filter_ctx.get("value")
        if field_ref and value is not None:
            return ((str(field_ref), str(value)),)
        return ()

    pairs: list[tuple[str, str]] = []
    for filter_properties in filter_ctx.get("filters") or ():
        field_ref = filter_properties.get("field_ref")
        if not field_ref:
            continue
        if filter_properties.get("type") == "categorical":
            members = (filter_properties.get("values") or {}).get("members") or ()
            for member in members:
                pairs.append((str(field_ref), str(member)))
        else:
            # A non-categorical filter's own concrete value, when the harvester
            # recorded one -- disclosed as a best-effort read, since range/relative-
            # date/top_n/condition filters each carry a differently-shaped `values`
            # document §4.1.1 does not standardise further.
            values = filter_properties.get("values") or {}
            for key in ("value", "min", "anchor"):
                if key in values:
                    pairs.append((str(field_ref), str(values[key])))
                    break
    return tuple(pairs)


def to_sdk_parameters(param_values: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    return tuple((str(name), str(value)) for name, value in param_values.items() if value is not None)


def build_dax_query(
    *,
    grain: tuple[str, ...],
    measures: tuple[str, ...],
    sdk_filters: tuple[tuple[str, str], ...],
    sdk_parameters: tuple[tuple[str, str], ...],
    table_map: dict[str, str],
) -> str:
    """§10.2's own worked-example shape, from real grain/measures/filters/parameters.
    ``table_map`` is field name -> DAX table name, from a real `Field -> ModelTable`
    binding when one exists (honestly empty today -- see `case_execution.py`'s own
    docstring); a field absent from it is qualified against its own name, a disclosed
    placeholder, not a guess."""

    def column_ref(field: str) -> str:
        table = table_map.get(field, field)
        return f"'{table}'[{field}]"

    body: list[str] = [f"    {column_ref(dim)}," for dim in grain]

    grouped: dict[str, list[str]] = {}
    for field, value in (*sdk_filters, *sdk_parameters):
        grouped.setdefault(field, []).append(value)
    for field, values in grouped.items():
        if len(values) == 1:
            body.append(f'    TREATAS({{"{values[0]}"}}, {column_ref(field)}),')
        else:
            quoted = ", ".join(f'"{v}"' for v in values)
            body.append(f"    FILTER(ALL({column_ref(field)}), {column_ref(field)} IN {{{quoted}}}),")

    for measure in measures:
        body.append(f'    "{measure}", [{measure}],')

    if body:
        body[-1] = body[-1].rstrip(",")

    lines = ["EVALUATE", "SUMMARIZECOLUMNS(", *body, ")"]
    if grain:
        lines.append(f"ORDER BY {', '.join(column_ref(dim) for dim in grain)}")
    return "\n".join(lines)


__all__ = ["build_dax_query", "to_sdk_filters", "to_sdk_parameters"]
