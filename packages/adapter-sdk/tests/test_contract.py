"""The published interface — S2.1.1 criterion 1.

"The SourceAdapter interface in §6.1 is published as a Python package with typed
methods: discover, fetch_workbook, parse, execute_case, capture_visual, capabilities"
"""

from __future__ import annotations

import inspect

import pytest

from astra_adapter import (
    BACKLOG_METHOD_NAMES,
    INTERFACE_VERSION,
    AdapterError,
    Capabilities,
    Column,
    ColumnRole,
    ParityRunStamp,
    Scope,
    SourceAdapter,
    UnsupportedCapability,
)
from astra_adapter.fake import build
from astra_adapter.proof import ExecutionStrategy, ResultSet, VisualCase

CONTRACT_METHODS = (
    "manifest",
    "enumerate",
    "fetch",
    "parse",
    "parse_calc",
    "usage",
    "owners",
    "viewers",
    "sites",
    "execute_case",
    "capture_visual",
)


def test_every_method_the_story_names_is_on_the_interface() -> None:
    """S2.1.1's six names, mapped to §6.1's, which win.

    The backlog and the specification disagree about what to call these. The backlog's own
    rule is that the specification wins (ADR 0004), so §6.1's names are the ones on the
    protocol — but a mapping kept only in prose rots, and the next person to read the story
    would have to take it on trust that ``fetch_workbook`` is present under another name.
    """
    for backlog_name, spec_name in BACKLOG_METHOD_NAMES.items():
        assert hasattr(SourceAdapter, spec_name), (
            f"story S2.1.1 asks for {backlog_name!r}, mapped to §6.1's {spec_name!r}, "
            f"which is not on the interface"
        )


def test_the_interface_is_the_whole_of_section_6_1() -> None:
    for name in CONTRACT_METHODS:
        assert hasattr(SourceAdapter, name), f"§6.1 declares {name} and the protocol omits it"


def test_the_package_carries_no_platform_dependency() -> None:
    """ "A second source can be added without changing the platform" (E2's goal).

    That claim rests on the SDK being installable on its own. If ``astra_adapter`` imported
    anything from ``astra_graph``, an adapter author would need the platform's source to
    build an adapter, and the direction of the dependency would be exactly backwards.
    """
    import astra_adapter

    for module in list(__import__("sys").modules):
        if module.startswith("astra_adapter"):
            source = __import__("sys").modules[module]
            text = inspect.getsource(source) if getattr(source, "__file__", None) else ""
            assert "astra_graph" not in text, f"{module} imports the platform"
    assert astra_adapter.__version__


def test_the_fake_satisfies_the_protocol() -> None:
    assert isinstance(build(), SourceAdapter)


async def test_the_fake_implements_every_method_for_real() -> None:
    """``isinstance`` against a Protocol only checks that the names exist.

    ``runtime_checkable`` protocols do not check signatures, so an adapter with a
    ``parse`` attribute set to ``None`` passes the isinstance test. Calling each method is
    the only way to know the interface is implemented rather than merely spelled.
    """
    adapter = build()
    scope = Scope(site="conformance")

    manifest = adapter.manifest()
    assert manifest.interface_version == INTERFACE_VERSION

    refs = [ref async for ref in adapter.enumerate(scope)]
    assert refs

    raw = await adapter.fetch(refs[0])
    assert isinstance(raw.payload, bytes) and raw.payload
    assert (await adapter.parse(raw)).parse_quality == 1.0
    assert (await adapter.parse_calc("SUM([Amount])")).root.name == "SUM"
    assert await adapter.usage(scope, 90)
    assert await adapter.owners(scope)
    assert await adapter.sites(scope)


async def test_a_fetch_result_is_bytes_not_an_object() -> None:
    """§6.1: "bytes + metadata, content-hashed".

    Typed ``Any`` before S2.1.1, and the fixture put a Python object in it — which works
    only while the adapter shares a process with its caller. An adapter running out of
    process cannot, and this is the test that would have caught it.
    """
    adapter = build()
    ref = await anext(adapter.enumerate(Scope(site="conformance")))
    raw = await adapter.fetch(ref)

    assert isinstance(raw.payload, bytes)
    assert raw.size_bytes == len(raw.payload)
    assert raw.media_type
    # Parsing must work from the bytes alone, which is what crossing a wire means.
    from astra_adapter.contract import RawAsset

    detached = RawAsset(
        ref=raw.ref,
        content_hash=raw.content_hash,
        payload=bytes(raw.payload),
        size_bytes=raw.size_bytes,
        media_type=raw.media_type,
    )
    assert (await adapter.parse(detached)).workbook_key


async def test_an_unclaimed_capability_is_refused_by_name() -> None:
    """§6.1 makes an absent capability a fact, not a defect — but a named one."""
    from astra_adapter.fake import FixtureSourceAdapter, build_site

    adapter = FixtureSourceAdapter(
        [build_site("s", 1)],
        capabilities=Capabilities(extract_read=False, live_query=False, screenshot=False),
    )
    with pytest.raises(UnsupportedCapability) as caught:
        await adapter.capture_visual(VisualCase(id="v", workbook_luid="x", view_name="s"))

    assert caught.value.capability == "capture_visual"
    assert isinstance(caught.value, AdapterError), "an unsupported capability is still an error"
    assert not caught.value.retryable, "retrying will not make an adapter support something"


# ------------------------------------------------------- S2.1.1 criterion 4


async def test_a_result_set_carries_the_interface_version() -> None:
    """ "The interface version is recorded on every harvest and every ParityRun."

    A ParityRun is E7's and does not exist. What exists is what it will be assembled from,
    and stamping the result set is what makes an unstamped run impossible rather than
    merely discouraged.
    """
    adapter = build()
    ref = await anext(adapter.enumerate(Scope(site="conformance")))
    from astra_adapter.proof import ParityCase

    result = await adapter.execute_case(ParityCase(id="c1", workbook_luid=ref.luid))

    assert result.interface_version == INTERFACE_VERSION
    assert result.adapter_name and result.adapter_version
    assert ParityRunStamp.from_results([result]).interface_version == INTERFACE_VERSION


def test_a_run_assembled_from_two_adapter_builds_is_refused() -> None:
    """One run, one adapter build. Otherwise "which adapter produced this verdict" has two
    answers and the stamp records neither."""

    def result(version: str) -> ResultSet:
        return ResultSet(
            case_id="c",
            columns=(Column("a", ColumnRole.DIMENSION, "string"),),
            rows=(),
            strategy=ExecutionStrategy.EXTRACT_READ,
            interface_version=INTERFACE_VERSION,
            adapter_name="fake",
            adapter_version=version,
        )

    with pytest.raises(ValueError, match="different adapter builds"):
        ParityRunStamp.from_results([result("0.1.0"), result("0.2.0")])


def test_a_stamp_needs_at_least_one_result() -> None:
    with pytest.raises(ValueError, match="at least one result"):
        ParityRunStamp.from_results([])


def test_the_fingerprint_ignores_how_and_when_a_result_was_obtained() -> None:
    """§6.3's determinism check compares content. A strategy or a timestamp differing
    between runs is not a determinism failure, and comparing whole objects makes it one."""

    def result(strategy: ExecutionStrategy, at: str) -> ResultSet:
        return ResultSet(
            case_id="c",
            columns=(
                Column("desk", ColumnRole.DIMENSION, "string"),
                Column("amount", ColumnRole.MEASURE, "real"),
            ),
            rows=(("rates", 1.0),),
            strategy=strategy,
            interface_version=INTERFACE_VERSION,
            adapter_name="fake",
            adapter_version="0.1.0",
            executed_at=at,
        )

    first = result(ExecutionStrategy.EXTRACT_READ, "2026-09-03T10:00:00Z")
    second = result(ExecutionStrategy.LIVE_REPLAY, "2026-09-03T11:00:00Z")
    assert first.fingerprint == second.fingerprint
    assert first != second
