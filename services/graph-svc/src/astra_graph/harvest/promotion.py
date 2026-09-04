"""The promotion gate, at the point where an adapter first touches an estate.

S2.1.2: *"A failing conformance run blocks adapter promotion to a tenant."*

Promotion is recorded through `POST /v1/adapters/{name}:promote`, which refuses without a
passing report. This module is the other half — making the promotion *mean* something. A
promotion record nothing consults is a record, not a gate, and the difference only shows up
on the day somebody skips the step.

**Why the check is here and not in the API layer.** A harvest can be started by a schedule
(S1.2.4) as well as by a request, and a gate that only covered the endpoint would be a gate
that a nightly run walks around. The Harvester is the one place every harvest passes through.

**Why it is a port.** The gate needs the tenant's promotion records, which live in
`adapters.conformance`; the Harvester needs no other database. A protocol keeps the harvest
package from importing the conformance store, and lets the unit suite state the gate's
answer directly rather than seeding rows to imply it.
"""

from __future__ import annotations

import logging
from typing import Protocol

from ..adapters.contract import AdapterError, AdapterManifest

logger = logging.getLogger(__name__)


class AdapterNotPromoted(AdapterError):
    """This adapter build is not enabled on this tenant.

    An `AdapterError` so it travels the paths a harvest already has for adapter problems, but
    it is deliberately **not retryable**: retrying changes nothing, and the fix is a person
    recording a conformance report and promoting the build.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message, retryable=False)


class PromotionGate(Protocol):
    async def require_promoted(self, manifest: AdapterManifest) -> None:
        """Raise `AdapterNotPromoted` unless this exact build may harvest."""
        ...


class UngatedPromotions:
    """No gate. The default, and it says so.

    Used where the gate cannot apply — the unit suite, and the fixture adapter, which
    generates its estate and has no client to protect. Named rather than expressed as
    ``promotions=None`` so that a deployment running ungated is a thing somebody chose.
    """

    async def require_promoted(self, manifest: AdapterManifest) -> None:
        return None


class TenantPromotionGate:
    """Refuses a harvest by an adapter build this tenant has not promoted."""

    def __init__(self, store: object, *, exempt: frozenset[str] = frozenset()) -> None:
        self._store = store
        self._exempt = exempt

    async def require_promoted(self, manifest: AdapterManifest) -> None:
        if manifest.name in self._exempt:
            return

        from ..adapters.conformance import AdapterBuild

        build = AdapterBuild(
            name=manifest.name,
            version=manifest.version,
            interface_version=manifest.interface_version,
            grammar_version=manifest.grammar_version,
        )
        promotion = await self._store.promotion(manifest.name)  # type: ignore[attr-defined]

        if promotion is None:
            raise AdapterNotPromoted(
                f"{build.describe()} is not promoted on this tenant, so it may not harvest. "
                f"§6.1 requires a passing conformance run before an adapter is enabled: run "
                f"`astra-adapter conformance --adapter {manifest.name} --remote --out "
                f"report.json`, POST it to /v1/adapters/conformance, then promote the build."
            )

        if promotion.build != build:
            # The dangerous case, and the reason the gate compares the whole build rather
            # than the name: an image swap under a promoted name would otherwise harvest a
            # client's estate on the strength of a report about different code.
            raise AdapterNotPromoted(
                f"{build.describe()} is running, but this tenant promoted "
                f"{promotion.build.describe()}. A conformance report is evidence about the "
                f"build that produced it; promote this build before harvesting with it."
            )

        logger.debug(
            "adapter promotion verified adapter=%s report=%s",
            build.describe(),
            promotion.report_id,
        )
