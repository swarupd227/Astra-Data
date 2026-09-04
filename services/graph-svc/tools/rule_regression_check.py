#!/usr/bin/env python
"""Rule regression guard — story S5.2.2.

    "Regression: every rule change re-runs the golden corpus and the PASSED artefacts that
    used the rule; any new failure blocks promotion."

The golden corpus already re-runs on every CI run via `pytest -m "not integration"`
(`tests/test_rules.py`) — every rule's own cases are checked on every build regardless of
which rule changed, so a shared-code change that breaks another rule's own case is already
caught there. This script is the other half: every already-produced `Measure` a shipped
rule wrote is re-rendered against the *current* rule set (`astra_graph.rules.
check_regression`) and compared to what is stored. A field that used to render and no
longer does is a regression and exits non-zero, the same "fail the build, name what broke"
shape `ontology_check.py`/`migration_check.py`/`contract_check.py` already established for
the ontology's own drift guards.

**What "the tenant" honestly means here.** This platform has no separate multi-tenant
promotion queue — one deployment is a client's own dev/test/prod environment (§12.2's own
three named workspaces), not a fleet advanced one at a time. "Promoted to the tenant on
merge" is this codebase's own ordinary release path: a merged, CI-green PR ships in the
next deployed image. Run against *this* repository's own CI graph (freshly migrated, no
history yet), this script is a smoke check — it has nothing to regress against and passes
trivially. Run by an operator against a real tenant's own accumulated graph before actually
promoting a new deployment to it, the same check has real teeth: exactly the artefacts that
deployment would otherwise silently break are named before it ships.

    python tools/rule_regression_check.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Output carries the specification's own punctuation — em dashes, and arrows in edge
# endpoint pairs. A console on a legacy code page cannot encode those, and a guard that
# crashes while reporting a difference is worse than no guard.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


SERVICE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE_ROOT / "src"))

from astra_graph.config import settings  # noqa: E402
from astra_graph.graph import create_pool  # noqa: E402
from astra_graph.rules import RegressionReport, check_regression  # noqa: E402


def _report(report: RegressionReport) -> None:
    print(
        f"checked {report.checked} artefact(s): {report.unchanged} unchanged, "
        f"{len(report.changed)} changed, {len(report.regressed)} regressed"
    )
    for c in report.changed:
        print(
            f"  changed: {c.calculated_field_id} was {c.previous_rule_id!r} "
            f"({c.previous_dax!r}) -> now {c.current_rule_id!r} ({c.current_dax!r})"
        )
    for r in report.regressed:
        print(f"  REGRESSED: {r.calculated_field_id} (measure {r.measure_id}, rule {r.rule_id!r}): {r.reason}")


async def _run() -> int:
    config = settings()
    pool = await create_pool(config)
    try:
        report = await check_regression(pool, config.graph_name)
    finally:
        await pool.close()

    _report(report)
    if not report.ok:
        print(f"\n{len(report.regressed)} regression(s) found — blocking promotion")
        return 1
    print("\nno regressions — safe to promote")
    return 0


def main() -> int:
    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
