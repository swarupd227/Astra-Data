"""The two execution strategies this deployment cannot perform, and why — S2.4.1.

`view_data` needs nothing but the REST API. The other two need something the platform does not
have, and each says so rather than pretending:

**Extract read** needs Tableau's Hyper API (`tableauhyperapi`), which is the only way to read a
``.hyper``. It is distributed under **Tableau's own licence, not an open-source one** — and
this platform's standing constraint is open-source components it can containerise freely. That
is a client decision, not an engineering one: a client who accepts Tableau's SDK terms installs
the package and this strategy becomes available with no code change. Until somebody makes that
decision, the reader reports its absence and the strategy is not claimed.

**Live replay** needs a database driver per connection class and network access to the
client's warehouse under their service account (§6.2: "under the client's service account").
Both arrive with E11 — Key Vault for the credential, and the egress policy §5.4 describes for
the route. The runner exists so the seam is visible and the code path is written; what it
cannot do is reach a warehouse nobody has given it.

**Why ports rather than silence.** A missing capability that reports itself is a fact the
Estate surface can show and a conformance report can skip (§6.1). A missing capability that
quietly returned an empty result set would be a parity case that passed against nothing.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from astra_adapter import Column, ParityCase, UnsupportedCapability

logger = logging.getLogger(__name__)


class ExtractReader(Protocol):
    """Reads a workbook's ``.hyper`` extract at a case's grain (§6.2's first strategy)."""

    @property
    def kind(self) -> str:
        """What is answering. Reported on the result so "nothing read it" and "the Hyper API
        read it" are never confused."""
        ...

    @property
    def available(self) -> bool: ...

    async def read(
        self, case: ParityCase
    ) -> tuple[tuple[Column, ...], tuple[tuple[Any, ...], ...], bool]: ...


class LiveQueryRunner(Protocol):
    """Runs the datasource's reconstructed SQL against the source connection."""

    @property
    def kind(self) -> str: ...

    @property
    def available(self) -> bool: ...

    async def run(
        self, case: ParityCase
    ) -> tuple[tuple[Column, ...], tuple[tuple[Any, ...], ...], bool, str]: ...


class NoExtractReader:
    """No Hyper API on this deployment.

    ``kind`` is ``"absent"`` rather than an empty string, so a result that says it was read by
    "absent" is obviously wrong rather than plausibly empty — the same reason
    `NullDirectoryResolver` and `NullMigrationUnitRegistry` report their kind on the platform
    side.
    """

    kind = "absent"
    available = False

    async def read(
        self, case: ParityCase
    ) -> tuple[tuple[Column, ...], tuple[tuple[Any, ...], ...], bool]:
        raise UnsupportedCapability(
            "extract_read",
            adapter="tableau",
        )

    @property
    def detail(self) -> str:
        return (
            "the Hyper API (tableauhyperapi) is not installed. It is the only way to read a "
            ".hyper extract and it ships under Tableau's licence rather than an open-source "
            "one, so installing it is a client decision. With it installed this strategy "
            "becomes available with no code change."
        )


class NoLiveQueryRunner:
    """No warehouse connectivity on this deployment."""

    kind = "absent"
    available = False

    async def run(
        self, case: ParityCase
    ) -> tuple[tuple[Column, ...], tuple[tuple[Any, ...], ...], bool, str]:
        raise UnsupportedCapability("live_query", adapter="tableau")

    @property
    def detail(self) -> str:
        return (
            "live replay needs a driver for the connection's class and network access to the "
            "client's warehouse under their service account (§6.2). Key Vault for the "
            "credential and the egress route arrive with E11."
        )


def describe(reader: object, runner: object) -> dict[str, Any]:
    """What the Estate surface shows about why a strategy is unavailable.

    An operator asking "why did every case come back inconclusive" should find the answer on
    Platform Health rather than in a log grep — and the answer is usually a licence decision
    nobody has made, which is not something they would guess.
    """
    return {
        "extract_read": {
            "available": bool(getattr(reader, "available", False)),
            "kind": getattr(reader, "kind", "absent"),
            "detail": getattr(reader, "detail", ""),
        },
        "live_replay": {
            "available": bool(getattr(runner, "available", False)),
            "kind": getattr(runner, "kind", "absent"),
            "detail": getattr(runner, "detail", ""),
        },
    }
