"""§10.6's own "at handover the suites are exported with a runner so the client can keep
running them without the platform" -- story S7.7.1.

**The runner does not re-implement the diff algorithm or the query construction -- it
vendors the real ones.** `diff.py`, `tolerance_rules.py` and `case_execution_query.py`
are all, confirmed by direct reading of their own imports, pure Python with zero
database or graph coupling (`diff.py` imports only `astra_adapter` and
`tolerance_rules`; the other two import neither). `tolerance_rules.py` and
`case_execution_query.py` (both S7.7.1) were each split out of a larger, DB-coupled
module for exactly this reason -- see their own docstrings; `tolerance_charter.py`
itself (the DB-coupled G1/versioning module) is *not* vendored, since nothing it adds
over `tolerance_rules.py` is needed standalone. Their live source bytes are copied
verbatim -- not a second, hand-written implementation -- which is what makes "without
the platform" true and keeps it true: the runner a client executes months after
handover is byte-for-byte the same algorithm this platform diffed and queried with,
never a fork that could quietly drift from it. The one mechanical exception is `diff.py`'s
own `from .tolerance_rules import ...` line: a *relative* import, valid only inside the
`astra_graph` package it normally lives in -- rewritten to `from tolerance_rules import
...` (no leading dot) when embedded, since the bundle is a flat folder of scripts, not a
package. A one-line, disclosed, mechanical substitution on the import statement itself;
no comparison or query-construction logic is touched.

**"Without the platform" still means "without graph-svc and its database" -- not
"without any Astra code."** The Source and Target adapters that actually reach Tableau
and Fabric are `astra-adapter-sdk` -- already, by design, an independently pip-
installable package (§6's own "a versioned worker image," `adapter-sdk/cli.py`'s own
`astra-adapter` command). `run_suite.py` loads them the identical way the SDK's own CLI
already does: `astra_adapter.registry.load_adapter(name)`. What the exported bundle
never needs is Postgres, Apache AGE, or graph-svc itself.

**`suite.json` carries the case *definitions* (sheet_ref, grain, measures, filter_ctx,
param_values), not a frozen snapshot of source/target rows.** A snapshot would let the
client compare two moments neither of which is "now" -- exactly the staleness §10.6
itself exists to catch. `CaseDerivationService.list_cases` (S7.2.1) is the one real,
already-tested source of this shape; the export reads it rather than re-deriving it.

**`charter.json` is the site's own charter at export time (`ToleranceCharterStore.
latest()`), via `ToleranceCharter.as_dict()`/`.from_dict()` (already round-trip-safe,
S7.1.1) -- not a copy frozen forever.** A client who tightens or loosens a tolerance
after handover edits this one file; `run_suite.py` never hard-codes it.
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import asyncpg

from .case_derivation import CaseDerivationService
from .tolerance_charter import ToleranceCharterStore

_PACKAGE_DIR = Path(__file__).parent
_VENDORED_MODULES = ("diff.py", "tolerance_rules.py", "case_execution_query.py")

#: Turns `diff.py`'s own in-package `from .tolerance_rules import ...` into a plain,
#: top-level import -- see this module's own docstring. The only vendored file with a
#: relative import: `tolerance_rules.py`/`case_execution_query.py` have none.
_RELATIVE_IMPORT = "from .tolerance_rules import"
_ABSOLUTE_IMPORT = "from tolerance_rules import"

EXPORT_KIND = "regression_export"
EXPORT_MEDIA_TYPE = "application/zip"

_RUN_SUITE_PY = '''\
#!/usr/bin/env python3
"""Astra Data regression runner -- exported at handover (spec section 10.6).

Re-executes this workbook's own parity suite against the source and target you point it
at, using the identical `diff_result_sets` (`diff.py`, vendored in this bundle
unmodified) the Astra Data platform itself diffed with. Requires no Astra Data service --
only `pip install astra-adapter-sdk` and working Source/Target adapters registered under
`astra_adapter.registry` (the same registry `astra-adapter list` already shows), the
identical way the SDK's own CLI already loads one.

Usage:
    python run_suite.py --source tableau --target fabric --workspace <fabric-workspace>

`suite.json` is this workbook's own case definitions; `charter.json` is the Tolerance
Charter to diff under -- edit it if your own tolerances have moved on since handover.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from astra_adapter import ParityCase as SdkParityCase
from astra_adapter.registry import load_adapter

from case_execution_query import build_dax_query, to_sdk_filters, to_sdk_parameters
from diff import diff_result_sets
from tolerance_rules import ToleranceCharter


async def _run_one(source, target, charter: ToleranceCharter, case: dict, *, workspace: str) -> dict:
    sdk_filters = to_sdk_filters(case.get("filter_ctx") or {})
    sdk_parameters = to_sdk_parameters(case.get("param_values") or {})
    sdk_case = SdkParityCase(
        id=case["case_key"],
        workbook_luid=case["mu_ref"],
        sheet=case.get("sheet_ref"),
        grain=tuple(case.get("grain") or ()),
        measures=tuple(case.get("measures") or ()),
        filters=sdk_filters,
        parameters=sdk_parameters,
    )
    expected = await source.execute_case(sdk_case)
    # No Field -> ModelTable binding is available outside the platform's own graph, so
    # every column is qualified against its own name -- the identical disclosed
    # placeholder `case_execution.build_dax_query` already falls back to internally
    # when no binding exists.
    query_text = build_dax_query(
        grain=sdk_case.grain, measures=sdk_case.measures,
        sdk_filters=sdk_filters, sdk_parameters=sdk_parameters, table_map={},
    )
    candidate = await target.evaluate(query_text=query_text, case=sdk_case, workspace=workspace)
    result = diff_result_sets(expected, candidate, charter)
    return {"case_key": case["case_key"], "sheet_ref": case.get("sheet_ref"), **result.as_dict()}


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", required=True, help="registered source adapter name, e.g. tableau")
    parser.add_argument("--target", required=True, help="registered target adapter name, e.g. fabric")
    parser.add_argument("--workspace", required=True, help="the Fabric workspace to evaluate against")
    parser.add_argument("--bundle-dir", default=str(Path(__file__).parent), help="where suite.json/charter.json live")
    args = parser.parse_args()

    bundle = Path(args.bundle_dir)
    suite = json.loads((bundle / "suite.json").read_text(encoding="utf-8"))
    charter = ToleranceCharter.from_dict(json.loads((bundle / "charter.json").read_text(encoding="utf-8")))

    source = load_adapter(args.source)
    target = load_adapter(args.target)

    results = []
    for case in suite["cases"]:
        try:
            results.append(await _run_one(source, target, charter, case, workspace=args.workspace))
        except Exception as exc:  # noqa: BLE001 -- one case's failure must not stop the suite
            results.append({"case_key": case.get("case_key"), "sheet_ref": case.get("sheet_ref"),
                             "result": "INCONCLUSIVE", "reason": f"{type(exc).__name__}: {exc}"})

    fails = [r for r in results if r["result"] == "FAIL"]
    print(json.dumps({"workbook_id": suite["workbook_id"], "cases_run": len(results),
                       "fail": len(fails), "results": results}, indent=2))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
'''

_README = """# Astra Data regression suite export

This bundle is this workbook's own retained parity suite (spec section 10.6), exported
at handover so it can keep running after the Astra Data platform is decommissioned.

## Contents

- `suite.json` -- every live parity case for this workbook (sheet, grain, measures,
  filters, parameters), as last derived.
- `charter.json` -- the Tolerance Charter this suite was diffing under at export time.
  Edit it if your own tolerances change.
- `diff.py`, `tolerance_rules.py`, `case_execution_query.py` -- the real Astra Data diff
  algorithm and query construction, copied verbatim. `run_suite.py` imports them
  directly; do not edit them, or a regression this bundle reports will no longer mean
  what the platform once meant by it.
- `run_suite.py` -- the runner. See its own `--help`.

## Requirements

    pip install astra-adapter-sdk

...plus whichever Source/Target adapter packages you already used during migration
(they register themselves with `astra_adapter.registry` on import -- the identical
mechanism the platform's own `astra-adapter` command line already uses).

## Running

    python run_suite.py --source tableau --target fabric --workspace <your-workspace>

Exits non-zero, and lists which cases FAILed, if a regression is found.
"""


async def build_regression_export(
    pool: asyncpg.Pool,
    graph_name: str,
    case_derivation: CaseDerivationService,
    charter_store: ToleranceCharterStore,
    *,
    workbook_id: str,
) -> bytes:
    """The zip's own bytes -- suite, charter, the vendored algorithm, the runner, a
    README. Raises `LookupError` if the workbook has no derived cases to export."""
    cases = await case_derivation.list_cases(workbook_id)
    if not cases:
        raise LookupError(f"workbook '{workbook_id}' has no derived parity cases to export")

    version = await charter_store.latest()
    exported_at = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("suite.json", json.dumps(
            {"workbook_id": workbook_id, "exported_at": exported_at, "cases": cases}, indent=2,
        ))
        archive.writestr("charter.json", json.dumps(version.charter.as_dict(), indent=2))
        for name in _VENDORED_MODULES:
            source = (_PACKAGE_DIR / name).read_text(encoding="utf-8")
            archive.writestr(name, source.replace(_RELATIVE_IMPORT, _ABSOLUTE_IMPORT))
        archive.writestr("run_suite.py", _RUN_SUITE_PY)
        archive.writestr("README.md", _README)
    return buffer.getvalue()


__all__ = ["EXPORT_KIND", "EXPORT_MEDIA_TYPE", "build_regression_export"]
