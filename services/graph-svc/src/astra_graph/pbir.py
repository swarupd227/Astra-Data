"""PBIR emission and schema validation -- stories S6.1.1 and S6.1.3, spec §7.1.

    "Report | PBIR (Power BI enhanced report format): definition/report.json,
    pages/*/page.json, visuals/*/visual.json, theme | Compositor | JSON schema validation
    against PBIR schema; visual-type whitelist; binding check that every field reference
    resolves in the model |"

**"The published PBIR JSON schema" is not vendored here.** Microsoft publishes the real
PBIR schema as part of the Fabric/Power BI Desktop tooling, under its own licence and its
own version cadence this platform has not pinned or committed to tracking yet -- the
identical honesty this codebase already applies to a real external dependency it cannot
reach for real (no live Fabric dev workspace exists for `tmdl`/`build.py` to validate
against either, S4.3.1). What `report.schema.json`/`page.schema.json`/`visual.schema.json`
(under `schemas/pbir/`) validate is real and enforced: the exact subset of PBIR structure
this Compositor actually emits today. Vendoring Microsoft's own published schema files is
real, disclosed future work -- see this story's own ADR.

**Emission is a pure function of already-gathered data**, the identical purity discipline
`tmdl.emit_tmdl` already established for the Modeller's own product: a PBIR bundle is
reproducible from a `ReportDefinition`/`Visual` set alone, not from whatever the graph
happens to say when emission runs.

**The whitelist and binding check are spec's own fuller requirement, not the acceptance
criteria's literal text** (which names schema validation only) -- both are cheap to check
at the same gate and both are already real facts this module has in hand (`resolve_visual`
already decided the visual type; every field well already carries `bound`), so they are
included as an honest superset of the AC rather than left for a story that may never
revisit this exact function.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from importlib import resources
from typing import Any

import jsonschema

#: Every target type `visual_mapping.DEFAULT_MAPPINGS` can produce, plus the visual-level
#: refinements `compositor.resolve_visual` can promote a base type into, plus the literal
#: placeholder type a redesign-flagged visual always carries.
VISUAL_TYPE_WHITELIST = frozenset(
    {
        "clusteredColumnChart", "stackedColumnChart", "clusteredBarChart", "stackedBarChart",
        "lineChart", "areaChart", "lineStackedColumnComboChart",
        "tableEx", "matrix", "scatterChart", "map", "filledMap", "pieChart", "card",
        "placeholder",
    }
)

_REPORT_SCHEMA_URI = "astra-data:pbir/report.schema.json"
_PAGE_SCHEMA_URI = "astra-data:pbir/page.schema.json"
_VISUAL_SCHEMA_URI = "astra-data:pbir/visual.schema.json"


def _load_schema(name: str) -> dict[str, Any]:
    text = resources.files("astra_graph.schemas.pbir").joinpath(name).read_text("utf-8")
    return dict(json.loads(text))


_REPORT_SCHEMA = _load_schema("report.schema.json")
_PAGE_SCHEMA = _load_schema("page.schema.json")
_VISUAL_SCHEMA = _load_schema("visual.schema.json")


def emit_pbir(*, visuals: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """One report's worth of PBIR documents, keyed by their path within the bundle.

    ``visuals`` is the same plain-dict shape ``compositor.compose_report`` already builds
    for its own return value (id/page/type/source_sheet_ref/encodings/redesign_flag/
    redesign_reason/layout/interactivity) -- one caller-facing shape, not a second one this
    function invents for itself.
    """
    pages: dict[str, list[str]] = {}
    bundle: dict[str, dict[str, Any]] = {}

    for visual in visuals:
        page = str(visual["page"])
        visual_id = str(visual["id"])
        pages.setdefault(page, []).append(visual_id)
        encodings = visual.get("encodings") or {}
        interactivity = visual.get("interactivity") or {}
        bundle[f"pages/{page}/visuals/{visual_id}/visual.json"] = {
            "$schema": _VISUAL_SCHEMA_URI,
            "name": visual_id,
            "visualType": visual.get("type"),
            "position": visual.get("layout"),
            "fieldWells": list(encodings.get("field_wells") or ()),
            "sort": list(encodings.get("sort") or ()),
            "filters": list(encodings.get("filters") or ()),
            "redesignFlag": bool(visual.get("redesign_flag")),
            "redesignReason": visual.get("redesign_reason"),
            "sourceSheetRef": visual.get("source_sheet_ref"),
            "interactivity": {
                "parameters": list(interactivity.get("parameters") or ()),
                "actions": list(interactivity.get("actions") or ()),
            },
        }

    for page, visual_ids in pages.items():
        bundle[f"pages/{page}/page.json"] = {
            "$schema": _PAGE_SCHEMA_URI,
            "name": page,
            "displayName": page,
            "visuals": visual_ids,
        }

    bundle["definition/report.json"] = {
        "$schema": _REPORT_SCHEMA_URI,
        "pages": [{"name": page} for page in pages],
    }
    return bundle


def validate_pbir(bundle: Mapping[str, dict[str, Any]]) -> tuple[list[str], list[str]]:
    """``(errors, warnings)`` -- schema validation and the visual-type whitelist are real
    defects (§7.1's own two named checks beyond schema); an unresolved field-well binding
    is a warning, not an error.

    That split matters today specifically: `Field->ModelTable` MAPS_TO edges have never
    been written by any story in this codebase (`compositor._resolve_bindings`'s own
    disclosed finding, unchanged from `generation.py`'s), so *every* plain dimension field
    on a shelf is unbound right now -- treating that as a schema failure would make
    `validation_state` read INVALID for nearly every real workbook, which would say "this
    report is malformed" about a report whose only problem is a pre-existing platform gap
    nothing in this story caused or can fix. The gap is real and worth surfacing --
    a warning does, without over-claiming what it means.
    """
    errors: list[str] = []
    warnings: list[str] = []

    for path, document in bundle.items():
        if path == "definition/report.json":
            schema = _REPORT_SCHEMA
        elif path.endswith("/page.json"):
            schema = _PAGE_SCHEMA
        elif path.endswith("/visual.json"):
            schema = _VISUAL_SCHEMA
        else:
            errors.append(f"{path}: not a recognised PBIR document path")
            continue
        for problem in jsonschema.Draft202012Validator(schema).iter_errors(document):
            errors.append(f"{path}: {problem.message}")

    for path, document in bundle.items():
        if not path.endswith("/visual.json"):
            continue
        visual_type = document.get("visualType")
        if visual_type not in VISUAL_TYPE_WHITELIST:
            errors.append(
                f"{path}: visual type {visual_type!r} is not on the whitelist "
                f"({', '.join(sorted(VISUAL_TYPE_WHITELIST))})"
            )
        for well in document.get("fieldWells") or ():
            if not well.get("bound"):
                warnings.append(
                    f"{path}: field well '{well.get('sourceName')}' ({well.get('shelf')}) "
                    f"does not resolve in the model -- {well.get('reason')}"
                )
        interactivity = document.get("interactivity") or {}
        for parameter in interactivity.get("parameters") or ():
            if not parameter.get("supported"):
                warnings.append(
                    f"{path}: parameter '{parameter.get('name')}' ({parameter.get('domain')}) "
                    f"is not translated -- {parameter.get('reason')}"
                )
        for action in interactivity.get("actions") or ():
            if not action.get("supported"):
                warnings.append(
                    f"{path}: {action.get('type')} action ({action.get('role')} of "
                    f"{action.get('otherSheets')}) is not translated -- {action.get('reason')}"
                )

    return errors, warnings


__all__ = ["VISUAL_TYPE_WHITELIST", "emit_pbir", "validate_pbir"]
