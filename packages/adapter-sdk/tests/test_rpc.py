"""The adapter RPC — S2.1.1 criterion 2.

    "Adapters run out of process and speak to the platform over the adapter RPC; an adapter
    crash does not take down a worker"

Two halves, tested two ways. The wire and the routes are tested in-process through an ASGI
transport, which is fast and exercises every codec. The crash isolation is tested against a
**real child process that is really killed**, because a mocked crash proves nothing about
what happens when a parser segfaults.
"""

from __future__ import annotations

import asyncio
import sys

import httpx
import pytest

from astra_adapter import AdapterError, Scope
from astra_adapter.contract import Capabilities
from astra_adapter.fake import FixtureSourceAdapter, build, build_site
from astra_adapter.proof import ParityCase, VisualCase
from astra_adapter.rpc import (
    AdapterSupervisor,
    InterfaceMismatch,
    RemoteAdapter,
    SupervisedAdapter,
    create_app,
    wire,
)


def remote_for(adapter: object) -> RemoteAdapter:
    """A `RemoteAdapter` speaking to an in-process ASGI app.

    The same client the platform uses, over a transport that skips the socket. Everything
    below the socket — encoding, routing, error translation — is the same code path.
    """
    transport = httpx.ASGITransport(app=create_app(adapter))  # type: ignore[arg-type]
    return RemoteAdapter(
        "http://adapter",
        client=httpx.AsyncClient(transport=transport, base_url="http://adapter"),
    )


# ------------------------------------------------------------------ the contract, remotely


async def test_the_whole_contract_survives_the_wire() -> None:
    """Every §6.1 method, through the RPC, compared to the same call in process.

    Written as a comparison rather than as assertions about shapes because the property that
    matters is that the platform cannot tell the difference — S2.1.1's point is that
    isolation is a deployment fact, not a change to how the platform is written.
    """
    local = build()
    scope = Scope(site="conformance")

    async with remote_for(local) as remote:
        assert remote.manifest() == local.manifest()

        near = [ref async for ref in local.enumerate(scope)]
        far = [ref async for ref in remote.enumerate(scope)]
        assert near == far

        assert await remote.fetch(far[0]) == await local.fetch(near[0])
        assert await remote.parse(await remote.fetch(far[0])) == await local.parse(
            await local.fetch(near[0])
        )
        assert await remote.parse_calc("SUM([x])") == await local.parse_calc("SUM([x])")
        assert list(await remote.usage(scope, 90)) == list(await local.usage(scope, 90))
        assert list(await remote.owners(scope)) == list(await local.owners(scope))
        assert list(await remote.viewers(scope, 90)) == list(await local.viewers(scope, 90))
        assert list(await remote.sites(scope)) == list(await local.sites(scope))

        case = ParityCase(id="c1", workbook_luid=far[0].luid)
        assert await remote.execute_case(case) == await local.execute_case(case)

        visual = VisualCase(id="v1", workbook_luid=far[0].luid, view_name="sheet 0")
        assert await remote.capture_visual(visual) == await local.capture_visual(visual)


async def test_bytes_survive_the_wire_unchanged() -> None:
    """JSON has no bytes. A .twbx that arrives corrupted parses into a wrong graph rather
    than into an error, which is the worst available failure mode."""
    local = build()
    scope = Scope(site="conformance")
    async with remote_for(local) as remote:
        ref = await anext(remote.enumerate(scope))
        raw = await remote.fetch(ref)

        assert raw.payload == (await local.fetch(ref)).payload
        assert raw.content_hash == (await local.fetch(ref)).content_hash


# ------------------------------------------------------------------- errors as answers


async def test_a_per_asset_failure_arrives_as_a_per_asset_error() -> None:
    """S1.2.1's "failures do not stop the run", across a process boundary."""
    site = build_site("s", 3)
    site.workbooks[1].fails_on = "fetch"
    adapter = FixtureSourceAdapter([site])

    parsed, failed = 0, []
    async with remote_for(adapter) as remote:
        async for ref in remote.enumerate(Scope(site="s")):
            try:
                await remote.parse(await remote.fetch(ref))
                parsed += 1
            except AdapterError as exc:
                failed.append((ref.luid, str(exc)))

    assert parsed == 2
    assert len(failed) == 1
    assert "could not download" in failed[0][1], "the adapter's own message must survive"


async def test_an_unsupported_capability_stays_unsupported_across_the_wire() -> None:
    """Not merely an error. The Estate surface shows "this deployment cannot do that", and
    an ``UnsupportedCapability`` flattened into a generic failure would be shown as a fault."""
    from astra_adapter import UnsupportedCapability

    adapter = FixtureSourceAdapter(
        [build_site("s", 1)], capabilities=Capabilities(extract_read=True, screenshot=False)
    )
    async with remote_for(adapter) as remote:
        with pytest.raises(UnsupportedCapability):
            await remote.capture_visual(
                VisualCase(id="v", workbook_luid="s-wb-00000", view_name="x")
            )


async def test_a_bug_in_the_adapter_becomes_a_readable_answer() -> None:
    """A crash inside a route must not be an empty 500.

    "The harvest failed" with no reason attached is the failure mode this story removes, so
    an unexpected exception is caught at the boundary and returned with its traceback.
    """

    class Broken(FixtureSourceAdapter):
        async def owners(self, scope: Scope):  # type: ignore[override]
            raise ZeroDivisionError("a real bug, not an AdapterError")

    async with remote_for(Broken([build_site("s", 1)])) as remote:
        with pytest.raises(AdapterError, match="a real bug"):
            await remote.owners(Scope(site="s"))


async def test_an_unreachable_adapter_is_retryable() -> None:
    """ "Connection refused" honestly means "not now", not "not ever" — a supervisor
    restarting a crashed adapter makes the next attempt succeed."""
    remote = RemoteAdapter("http://127.0.0.1:1")  # nothing listens on port 1
    with pytest.raises(AdapterError) as caught:
        await remote.connect()
    assert caught.value.retryable
    assert "unreachable" in str(caught.value)
    await remote.aclose()


# --------------------------------------------------------------- interface versioning


async def test_an_adapter_built_against_another_interface_is_refused() -> None:
    """Refused, not negotiated. A platform that quietly accepted an older adapter would be
    deciding at runtime which parts of the contract still hold."""
    adapter = FixtureSourceAdapter([build_site("s", 1)])
    manifest = adapter.manifest()
    object.__setattr__(manifest, "interface_version", "0.9")  # frozen dataclass

    class Old(FixtureSourceAdapter):
        def manifest(self):  # type: ignore[override]
            return manifest

    remote = remote_for(Old([build_site("s", 1)]))
    with pytest.raises(InterfaceMismatch, match="0.9"):
        await remote.connect()
    await remote.aclose()


async def test_the_adapter_refuses_a_caller_speaking_another_interface() -> None:
    """Both directions. The adapter is the one that knows its own contract version, and a
    newer platform calling an older adapter is the same mistake from the other end."""
    transport = httpx.ASGITransport(app=create_app(build()))
    async with httpx.AsyncClient(transport=transport, base_url="http://adapter") as client:
        response = await client.post(
            "/v1/enumerate", json={"scope": {"site": "conformance"}, "interface_version": "9.9"}
        )
    assert response.status_code == 500
    assert "9.9" in response.json()["message"]


def test_the_wire_round_trips_every_type() -> None:
    """The wire format is the contract's compatibility surface, so it is written by hand —
    which means every codec needs a test, because nothing generates them."""
    from astra_adapter.contract import AssetRef, RawAsset

    asset = AssetRef(
        luid="wb-1",
        name="Daily VaR",
        site="rqa",
        project="Risk",
        revision="3",
        project_path=("Risk", "Market"),
        updated_at="2026-01-01T00:00:00Z",
    )
    assert wire.decode_asset(wire.encode_asset(asset)) == asset

    raw = RawAsset(ref=asset, content_hash="abc", payload=b"\x00\x01\xff", size_bytes=3)
    assert wire.decode_raw_asset(wire.encode_raw_asset(raw)) == raw

    scope = Scope(site="rqa", project="Risk")
    assert wire.decode_scope(wire.encode_scope(scope)) == scope


# --------------------------------------------------------- out of process, and crashing


@pytest.mark.process
async def test_an_adapter_really_runs_in_another_process() -> None:
    supervisor = AdapterSupervisor("fake")
    try:
        process = await supervisor.start()
        assert process.alive
        assert process.process is not None
        assert process.process.pid != __import__("os").getpid()

        async with supervisor.adapter() as remote:
            assert remote.manifest().name == "fake"
            assert [ref async for ref in remote.enumerate(Scope(site="conformance"))]
    finally:
        await supervisor.stop()


@pytest.mark.process
async def test_a_crashed_adapter_does_not_take_down_the_worker() -> None:
    """S2.1.1 criterion 2, stated as plainly as it can be tested.

    The adapter process is killed outright in the middle of a harvest-shaped loop, the way a
    segfault in a parser's C extension would kill it. The caller — standing in for the
    harvest worker — must survive.

    It does better than survive: **nothing is lost at all**. The supervisor notices the dead
    process on the next call and restarts it before that call goes out, so a crash between
    two units of work costs a process launch and no data. That is worth asserting exactly,
    because the weaker claim ("the worker survives, one asset fails") would still pass if the
    restart were broken and every subsequent asset failed instead.
    """
    supervisor = AdapterSupervisor("fake")
    worker = SupervisedAdapter(supervisor)
    try:
        refs = [ref async for ref in worker.enumerate(Scope(site="conformance"))]
        assert len(refs) >= 4
        first_pid = (await supervisor.ensure_running()).process.pid  # type: ignore[union-attr]

        parsed: list[str] = []
        failed: list[str] = []
        for index, ref in enumerate(refs):
            if index == 2:
                process = await supervisor.ensure_running()
                assert process.process is not None
                process.process.kill()
                await process.process.wait()
            try:
                await worker.parse(await worker.fetch(ref))
                parsed.append(ref.luid)
            except AdapterError as exc:
                failed.append(ref.luid)
                assert exc.retryable, "a dead adapter is retryable; it can be restarted"

        # This line running at all is the criterion. The rest is how well it was met.
        assert not failed
        assert parsed == [ref.luid for ref in refs]

        running = await supervisor.ensure_running()
        assert running.restarts >= 1
        assert running.process is not None and running.process.pid != first_pid
    finally:
        await worker.aclose()


@pytest.mark.process
async def test_a_call_to_an_adapter_that_has_died_fails_that_call_and_nothing_else() -> None:
    """The harder half: a call that is already on its way when the process dies.

    Nothing can rescue it — the bytes are gone, and inventing a result would be far worse
    than reporting one failure. What must hold is that it fails as a *retryable per-asset*
    ``AdapterError``, which is what the Harvester already records against one workbook before
    carrying on (S1.2.1), rather than as an exception that unwinds the run.

    Driven through a plain `RemoteAdapter` with no supervisor, so the death is not noticed
    and repaired first — this is the raw case, deterministically.
    """
    supervisor = AdapterSupervisor("fake")
    try:
        process = await supervisor.start()
        remote = RemoteAdapter(process.base_url, timeout=5.0)
        await remote.connect()

        refs = [ref async for ref in remote.enumerate(Scope(site="conformance"))]
        assert await remote.fetch(refs[0]), "healthy before the kill"

        assert process.process is not None
        process.process.kill()
        await process.process.wait()

        with pytest.raises(AdapterError) as caught:
            await remote.fetch(refs[1])
        assert caught.value.retryable
        assert "unreachable" in str(caught.value)

        await remote.aclose()
    finally:
        await supervisor.stop()


@pytest.mark.process
async def test_an_adapter_that_will_not_start_is_reported_rather_than_retried_forever() -> None:
    """A bound, not a retry policy. An adapter that crashes on the first call of every run
    is broken, and restarting it forever turns a loud failure into a harvest that never
    finishes."""
    supervisor = AdapterSupervisor(
        "nonexistent",
        command=[sys.executable, "-c", "import sys; sys.exit(3)"],
        startup_timeout=5.0,
    )
    with pytest.raises(AdapterError) as caught:
        await supervisor.start(attempts=1)
    assert "did not start" in str(caught.value)
    assert not caught.value.retryable
    await supervisor.stop()


@pytest.mark.process
async def test_the_restart_bound_is_enforced() -> None:
    supervisor = AdapterSupervisor("fake", max_restarts=1)
    try:
        for _ in range(2):
            process = await supervisor.ensure_running()
            assert process.process is not None
            process.process.kill()
            await process.process.wait()

        with pytest.raises(AdapterError, match="crashed"):
            await supervisor.ensure_running()
    finally:
        await supervisor.stop()


@pytest.mark.process
async def test_two_supervised_adapters_do_not_fight_over_a_port() -> None:
    """Ports come from the OS, and the window between asking and binding is real."""
    first, second = AdapterSupervisor("fake"), AdapterSupervisor("fake")
    try:
        a, b = await asyncio.gather(first.start(), second.start())
        assert a.port != b.port
        assert a.alive and b.alive
    finally:
        await asyncio.gather(first.stop(), second.stop())
