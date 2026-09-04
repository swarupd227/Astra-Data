"""Conformance reports and the promotion gate — story S2.1.2.

    "Suite output is a signed report stored in the artefact store and linked from Platform
    Health. A failing conformance run blocks adapter promotion to a tenant."

The suite itself is the SDK's and is tested there. What is tested here is the platform's
half: that a report is kept whole, that a failing one blocks promotion, that the block is not
a formality an image swap walks around, and that Platform Health says which of those
conditions a deployment is in.
"""

from __future__ import annotations

import pytest

from astra_graph.adapters.conformance import (
    AdapterBuild,
    InMemoryConformanceStore,
    PromotionError,
    check_promotable,
)
from astra_graph.adapters.contract import AdapterManifest, Capabilities
from astra_graph.harvest import AdapterNotPromoted, TenantPromotionGate

from .conftest import ARTIZENT_HEADERS

BUILD = AdapterBuild(
    name="tableau", version="1.4.0", interface_version="1.0", grammar_version="tableau-3"
)


def signed_report(
    *,
    adapter: str = "tableau",
    version: str = "1.4.0",
    interface: str = "1.0",
    grammar: str | None = "tableau-3",
    passed: bool = True,
    failures: tuple[str, ...] = (),
    signed: bool = True,
) -> dict:
    """A report shaped exactly as `astra-adapter conformance --out` writes it."""
    checks = [
        {"name": "discovery completeness", "outcome": "PASSED", "summary": "all assets", "detail": []},
        {"name": "throttling", "outcome": "PASSED", "summary": "backs off", "detail": []},
    ]
    for name in failures:
        checks.append(
            {"name": name, "outcome": "FAILED", "summary": f"{name} did not hold", "detail": []}
        )
    return {
        "report": {
            "adapter": adapter,
            "adapter_version": version,
            "interface_version": interface,
            "grammar_version": grammar,
            "corpus": "tableau/acme-sample",
            "passed": passed,
            "counts": {"PASSED": 2, "FAILED": len(failures), "SKIPPED": 0},
            "checks": checks,
        },
        "content_hash": "sha256:" + "a" * 64,
        "signed": signed,
        "signature": "b" * 64 if signed else None,
        "algorithm": "hmac-sha256" if signed else None,
        "key_id": "env" if signed else "no signing key",
    }


def manifest(build: AdapterBuild) -> AdapterManifest:
    return AdapterManifest(
        name=build.name,
        version=build.version,
        grammar_version=build.grammar_version or "",
        interface_version=build.interface_version,
        capabilities=Capabilities(extract_read=True, usage=True, ownership=True),
    )


@pytest.fixture
def store() -> InMemoryConformanceStore:
    return InMemoryConformanceStore()


def _app(client):
    """The FastAPI app behind the test client, so a test can set the store it needs."""
    return client._transport.app


# ---------------------------------------------------------------- storing a report


async def test_a_report_is_stored_whole(store) -> None:
    """"The adapter passed" is not evidence; the report is.

    An engineer asking six months later why an adapter was allowed onto a client's estate
    needs the checks that ran, what they found, and the corpus they ran against.
    """
    record = await store.record(signed_report(), principal="user:p.eng@artizent.example")

    assert record.passed
    assert record.build == BUILD
    assert record.corpus == "tableau/acme-sample"
    assert record.content_hash.startswith("sha256:")
    assert record.signed
    assert len(record.report["checks"]) == 2
    assert record.recorded_by == "user:p.eng@artizent.example"


async def test_a_failing_report_is_stored_too(store) -> None:
    """A failing run is the reason a promotion was refused. Discarding it would leave the
    platform able to say "no" but not "why"."""
    record = await store.record(
        signed_report(passed=False, failures=("parse round-trip", "throttling")),
        principal="agent:ci",
    )

    assert not record.passed
    assert record.checks_failed == 2
    assert record.failures == [
        "parse round-trip: parse round-trip did not hold",
        "throttling: throttling did not hold",
    ]


async def test_a_report_that_names_no_build_is_refused(store) -> None:
    """Nothing can be promoted on it, so accepting it would only make the gate harder to
    reason about."""
    nameless = signed_report()
    nameless["report"]["adapter_version"] = ""

    with pytest.raises(PromotionError, match="must name the adapter"):
        await store.record(nameless, principal="agent:ci")


# ------------------------------------------------------------------- the gate


async def test_a_failing_run_blocks_promotion(store) -> None:
    """S2.1.2 criterion 3, in its plainest form."""
    await store.record(signed_report(passed=False, failures=("throttling",)), principal="agent:ci")

    with pytest.raises(PromotionError) as caught:
        await store.promote(BUILD, reason="the client is waiting", principal="user:pm@x")

    assert "blocks promotion" in str(caught.value)
    assert "throttling" in str(caught.value), "name the check that failed"


async def test_an_adapter_with_no_report_cannot_be_promoted(store) -> None:
    """Different from a failing report and told apart in the message: nothing is *known*
    about this adapter, which is not the same as knowing it is broken."""
    with pytest.raises(PromotionError) as caught:
        await store.promote(BUILD, reason="it looked fine locally", principal="user:pm@x")

    assert "no conformance report" in str(caught.value)
    assert "astra-adapter conformance" in str(caught.value), "say how to get one"


async def test_a_passing_report_promotes(store) -> None:
    record = await store.record(signed_report(), principal="agent:ci")

    promotion = await store.promote(
        BUILD, reason="acme sample corpus passed in staging", principal="user:pm@x"
    )

    assert promotion.active
    assert promotion.report_id == record.id
    assert promotion.build == BUILD
    assert promotion.promoted_by == "user:pm@x"
    assert (await store.promotion("tableau")).id == promotion.id


async def test_a_report_for_another_build_does_not_promote_this_one(store) -> None:
    """The most likely route to promoting something nobody tested: a version bump for a
    "small fix", on the strength of the previous version's report."""
    await store.record(signed_report(version="1.3.0"), principal="agent:ci")

    with pytest.raises(PromotionError) as caught:
        await store.promote(BUILD, reason="only a small fix since 1.3", principal="user:pm@x")

    assert "1.3.0" in str(caught.value)
    assert "not" in str(caught.value)


async def test_a_grammar_change_alone_needs_a_new_report(store) -> None:
    """A grammar version is in the build because it changes what the adapter *reads*, which
    is the thing conformance is about."""
    await store.record(signed_report(grammar="tableau-2"), principal="agent:ci")

    with pytest.raises(PromotionError, match="tableau-2"):
        await store.promote(BUILD, reason="same adapter, newer grammar", principal="user:pm@x")


async def test_promoting_a_new_build_revokes_the_old_one(store) -> None:
    """One promoted build per adapter. Two would mean the platform could not say which
    adapter it is running, which is the question the record exists to answer."""
    await store.record(signed_report(version="1.3.0"), principal="agent:ci")
    await store.promote(
        AdapterBuild("tableau", "1.3.0", "1.0", "tableau-3"),
        reason="the first promotion",
        principal="user:pm@x",
    )
    await store.record(signed_report(), principal="agent:ci")

    await store.promote(BUILD, reason="upgrade to 1.4", principal="user:pm@x")

    active = await store.promotion("tableau")
    assert active.build.version == "1.4.0"
    assert len(await store.promotions()) == 1
    superseded = [p for p in store.history if p.build.version == "1.3.0" and not p.active]
    assert superseded, "the old promotion is revoked, not deleted"


async def test_revoking_keeps_the_record(store) -> None:
    """A conformance failure found after promotion has to be actionable, and "delete the
    row" is not an audit trail."""
    await store.record(signed_report(), principal="agent:ci")
    await store.promote(BUILD, reason="promoted for the pilot", principal="user:pm@x")

    revoked = await store.revoke(
        "tableau", reason="grammar regression on RAWSQL", principal="user:p.eng@x"
    )

    assert not revoked.active
    assert revoked.revocation_reason == "grammar regression on RAWSQL"
    assert revoked.promoted_by == "user:pm@x", "how it was promoted is still readable"
    assert await store.promotion("tableau") is None


# ----------------------------------------------- the gate at the point of harvest


async def test_an_unpromoted_adapter_cannot_harvest(store) -> None:
    """A promotion record nothing consults is a record, not a gate."""
    gate = TenantPromotionGate(store)

    with pytest.raises(AdapterNotPromoted) as caught:
        await gate.require_promoted(manifest(BUILD))

    assert "not promoted on this tenant" in str(caught.value)
    assert not caught.value.retryable, "retrying changes nothing; a person must promote it"


async def test_a_promoted_adapter_may_harvest(store) -> None:
    await store.record(signed_report(), principal="agent:ci")
    await store.promote(BUILD, reason="passed against the acme sample", principal="user:pm@x")

    await TenantPromotionGate(store).require_promoted(manifest(BUILD))


async def test_an_image_swap_under_a_promoted_name_is_refused(store) -> None:
    """The reason the gate compares the whole build and not the name.

    Otherwise a new image deployed under `tableau` harvests a client's estate on the
    strength of a report about different code — the failure the whole gate exists to stop,
    and the one a name check would miss.
    """
    await store.record(signed_report(version="1.3.0"), principal="agent:ci")
    await store.promote(
        AdapterBuild("tableau", "1.3.0", "1.0", "tableau-3"),
        reason="the promoted build",
        principal="user:pm@x",
    )

    with pytest.raises(AdapterNotPromoted) as caught:
        await TenantPromotionGate(store).require_promoted(manifest(BUILD))

    assert "1.4.0" in str(caught.value) and "1.3.0" in str(caught.value)


async def test_an_interface_bump_alone_needs_a_new_report(store) -> None:
    """S2.4.1 retyped `ResultSet.columns` and took the interface from 1.0 to 1.1, and the
    consequence lands here.

    The adapter's code did not regress and its version could plausibly have stayed the same,
    but every result set it now produces has a different shape. A report written against 1.0
    is evidence about an adapter that answered a different question, so the gate refuses the
    1.1 build until somebody re-runs the suite — which is ADR 0015's rule working: an
    interface change that removes or retypes a field is not additive, and the version bump is
    what makes the stale promotion visible instead of silent.
    """
    await store.record(signed_report(), principal="agent:ci")
    await store.promote(BUILD, reason="passed at interface 1.0", principal="user:pm@x")

    bumped = AdapterBuild("tableau", "1.4.0", "1.1", "tableau-3")
    with pytest.raises(AdapterNotPromoted) as caught:
        await TenantPromotionGate(store).require_promoted(manifest(bumped))

    message = str(caught.value)
    assert "interface 1.1" in message and "interface 1.0" in message
    assert "promote this build" in message


async def test_the_fixture_adapter_is_exempt_and_the_exemption_is_named(store) -> None:
    """The gate protects a *client's* estate. The fixture generates its own and has no
    client, so gating local development on a ceremony would protect nobody."""
    from astra_graph.harvest_setup import UNGATED_ADAPTERS

    gate = TenantPromotionGate(store, exempt=UNGATED_ADAPTERS)
    fixture = AdapterBuild("fixture", "0.1.0", "1.0", "fixture-1")

    await gate.require_promoted(manifest(fixture))
    assert "tableau" not in UNGATED_ADAPTERS, "a real adapter is never exempt"


async def test_the_gate_rule_is_one_function(store) -> None:
    """Both callers — the promote endpoint and the harvest gate — enforce the same rule
    rather than two similar ones."""
    with pytest.raises(PromotionError, match="no conformance report"):
        check_promotable(None, BUILD)

    failing = await store.record(signed_report(passed=False, failures=("x",)), principal="ci")
    with pytest.raises(PromotionError, match="blocks promotion"):
        check_promotable(failing, BUILD)

    passing = await store.record(signed_report(), principal="ci")
    check_promotable(passing, BUILD)


# --------------------------------------------------------------------- the API


async def test_the_api_records_a_report_and_refuses_to_promote_on_a_failing_one(
    client,
) -> None:
    _app(client).state.conformance_store = InMemoryConformanceStore()

    recorded = await client.post(
        "/v1/adapters/conformance",
        json=signed_report(passed=False, failures=("visual capture",)),
        headers=ARTIZENT_HEADERS,
    )
    assert recorded.status_code == 201
    assert recorded.json()["passed"] is False

    refused = await client.post(
        "/v1/adapters/tableau:promote",
        json={
            "adapter_version": "1.4.0",
            "interface_version": "1.0",
            "grammar_version": "tableau-3",
            "reason": "the pilot starts on Monday",
        },
        headers=ARTIZENT_HEADERS,
    )
    assert refused.status_code == 409
    body = refused.json()["detail"]
    assert body["error"] == "conformance_required"
    assert "visual capture" in body["message"]


async def test_the_api_promotes_on_a_passing_report(client) -> None:
    _app(client).state.conformance_store = InMemoryConformanceStore()
    await client.post(
        "/v1/adapters/conformance", json=signed_report(), headers=ARTIZENT_HEADERS
    )

    promoted = await client.post(
        "/v1/adapters/tableau:promote",
        json={
            "adapter_version": "1.4.0",
            "interface_version": "1.0",
            "grammar_version": "tableau-3",
            "reason": "passed against the acme sample corpus",
        },
        headers=ARTIZENT_HEADERS,
    )

    assert promoted.status_code == 200
    assert promoted.json()["active"] is True

    listing = await client.get("/v1/adapters", headers=ARTIZENT_HEADERS)
    assert listing.json()["count"] == 1
    assert listing.json()["promoted"][0]["adapter_version"] == "1.4.0"


async def test_promotion_needs_a_reason(client) -> None:
    """Every recorded decision in this platform carries one (§15.2)."""
    _app(client).state.conformance_store = InMemoryConformanceStore()
    await client.post(
        "/v1/adapters/conformance", json=signed_report(), headers=ARTIZENT_HEADERS
    )

    response = await client.post(
        "/v1/adapters/tableau:promote",
        json={"adapter_version": "1.4.0", "interface_version": "1.0", "reason": "ok"},
        headers=ARTIZENT_HEADERS,
    )

    assert response.status_code == 422


async def test_the_check_endpoint_answers_without_promoting(client) -> None:
    """A deployment pipeline wants to know before it swaps an image, not after."""
    _app(client).state.conformance_store = InMemoryConformanceStore()

    body = {
        "adapter_version": "1.4.0",
        "interface_version": "1.0",
        "grammar_version": "tableau-3",
        "reason": "a pipeline pre-flight",
    }
    before = await client.post("/v1/adapters/tableau:check", json=body, headers=ARTIZENT_HEADERS)
    assert before.json()["promotable"] is False
    assert "no conformance report" in before.json()["reason"]

    await client.post(
        "/v1/adapters/conformance", json=signed_report(), headers=ARTIZENT_HEADERS
    )
    after = await client.post("/v1/adapters/tableau:check", json=body, headers=ARTIZENT_HEADERS)
    assert after.json()["promotable"] is True
    assert (await client.get("/v1/adapters", headers=ARTIZENT_HEADERS)).json()["count"] == 0


async def test_a_report_can_be_read_back_in_full(client) -> None:
    """The link Platform Health carries, and what is behind it."""
    _app(client).state.conformance_store = InMemoryConformanceStore()
    created = await client.post(
        "/v1/adapters/conformance", json=signed_report(), headers=ARTIZENT_HEADERS
    )
    report_id = created.json()["id"]

    full = await client.get(
        f"/v1/adapters/conformance/{report_id}", headers=ARTIZENT_HEADERS
    )

    assert full.status_code == 200
    assert full.json()["report"]["checks"], "the checks that ran, not just a verdict"
    assert full.json()["signature"]


def attach_harvester(client, *, name: str = "tableau", version: str = "1.4.0"):
    """A harvester over an adapter with a *non-exempt* name.

    The suite's own `harvest_app` fixture runs the fixture adapter, which the gate exempts —
    so the three states Platform Health has to distinguish (exempt, unpromoted, running the
    wrong build) cannot all be reached through it.
    """
    from astra_graph.adapters.fixture import FixtureSourceAdapter, build_site
    from astra_graph.credentials import StaticCredentialProvider
    from astra_graph.harvest import Harvester, InMemoryHarvestStore
    from astra_graph.writes import GraphWriter

    from .fakes import InMemoryGraphRepository

    app = _app(client)
    app.state.harvester = Harvester(
        adapter=FixtureSourceAdapter(
            [build_site("rqa", 2)], name=name, grammar_version="tableau-3"
        ),
        writer=GraphWriter(InMemoryGraphRepository()),
        store=InMemoryHarvestStore(),
        credentials=StaticCredentialProvider({"tableau/rqa": "a-token"}),
        graph_name="astra_estate_test",
    )
    return app


async def test_platform_health_says_a_running_adapter_has_no_report(client) -> None:
    """S2.1.2 criterion 2. The condition worth seeing at a glance is a running adapter with
    no report: nothing is known about whether it works."""
    app = attach_harvester(client)
    app.state.conformance_store = InMemoryConformanceStore()

    health = (await client.get("/v1/platform/health", headers=ARTIZENT_HEADERS)).json()
    conformance = health["adapter"]["conformance"]

    assert conformance["available"] is True
    assert conformance["gated"] is True
    assert conformance["promoted"] is False
    assert conformance["report"] is None
    assert "not promoted on this tenant" in conformance["detail"]


async def test_platform_health_links_a_recorded_report(client) -> None:
    app = attach_harvester(client)
    app.state.conformance_store = InMemoryConformanceStore()
    running = app.state.harvester.manifest()
    await client.post(
        "/v1/adapters/conformance",
        json=signed_report(
            adapter=running.name,
            version=running.version,
            interface=running.interface_version,
            grammar=running.grammar_version,
            signed=False,
        ),
        headers=ARTIZENT_HEADERS,
    )

    health = (await client.get("/v1/platform/health", headers=ARTIZENT_HEADERS)).json()
    conformance = health["adapter"]["conformance"]

    assert conformance["report"]["passed"] is True
    assert conformance["link"] == f"/v1/adapters/conformance/{conformance['report']['id']}"
    assert conformance["report"]["signed"] is False


async def test_platform_health_shouts_when_the_running_build_is_not_the_promoted_one(
    client,
) -> None:
    """The loudest condition on the screen: the tenant approved one build and is running
    another, so everything the promotion attests to is about an image that is not here."""
    app = attach_harvester(client)
    store = InMemoryConformanceStore()
    app.state.conformance_store = store
    running = app.state.harvester.manifest()

    await store.record(
        signed_report(
            adapter=running.name,
            version="1.3.0",
            interface=running.interface_version,
            grammar=running.grammar_version,
        ),
        principal="agent:ci",
    )
    await store.promote(
        AdapterBuild(
            running.name, "1.3.0", running.interface_version, running.grammar_version
        ),
        reason="promoted before the upgrade",
        principal="user:pm@x",
    )

    health = (await client.get("/v1/platform/health", headers=ARTIZENT_HEADERS)).json()
    conformance = health["adapter"]["conformance"]

    assert conformance["promoted"] is False
    assert "What was approved is not what is running" in conformance["detail"]


async def test_platform_health_says_an_exempt_adapter_is_exempt(client, harvest_app) -> None:
    """The screen must agree with the gate.

    Reporting an exempt adapter as "not promoted" reads as "this harvest should be blocked
    and is not" — a defect an operator would go looking for and never find.
    """
    _app(client).state.conformance_store = InMemoryConformanceStore()

    health = (await client.get("/v1/platform/health", headers=ARTIZENT_HEADERS)).json()
    conformance = health["adapter"]["conformance"]

    assert conformance["gated"] is False
    assert "exempt from the promotion gate" in conformance["detail"]
    assert "A real source adapter is never exempt" in conformance["detail"]
