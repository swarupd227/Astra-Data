"""The fixture source adapter — now the SDK's fake source.

**This is not the Tableau adapter.** It exists so the Harvester can be built, tested and
measured before F2.2 to F2.4 deliver a live Tableau client, and so the local stack has
something to harvest.

It moved to ``astra_adapter.fake`` in S2.1.1, where §6.3's conformance suite runs against it:
a suite that has never been run against a passing adapter is an assertion, not a test, and
the fake is the adapter it is true about. Keeping the platform's import path unchanged means
the move is a packaging decision rather than a rewrite of every test that harvests something.
"""

from __future__ import annotations

from astra_adapter.fake import (
    FIXTURE_GRAMMAR_GAPS,
    FixtureSite,
    FixtureSourceAdapter,
    FixtureWorkbook,
    build_site,
)

__all__ = [
    "FIXTURE_GRAMMAR_GAPS",
    "FixtureSite",
    "FixtureSourceAdapter",
    "FixtureWorkbook",
    "build_site",
]
