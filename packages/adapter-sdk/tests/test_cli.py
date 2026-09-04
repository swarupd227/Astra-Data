"""``astra-adapter`` — S2.1.1 criterion 3.

    "astra-adapter conformance --adapter tableau runs the suite end to end"

The story named ``tableau`` before it existed, and these tests checked that the command said
so precisely rather than failing obscurely — "no such adapter" printed as "0 checks passed"
is how a tenant gets enabled against nothing.

**F2.2 has since built it**, so the interesting case moved: the command finds `tableau` when
its package is installed, and an unknown name is now stood in for by one that will not be
built for a long time. What these tests check is the *message*, not which adapters happen to
exist this month.
"""

from __future__ import annotations

import json

import pytest

from astra_adapter.cli import main
from astra_adapter.registry import UnknownAdapter, load_adapter, register, registered_names


def test_the_suite_runs_end_to_end_against_a_registered_adapter(capsys) -> None:
    exit_code = main(["conformance", "--adapter", "fake"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "CONFORMANT" in output
    assert "discovery completeness" in output
    assert "AST round-trip" in output


def test_the_report_is_machine_readable(capsys) -> None:
    """CI reads this. §6.3 runs the suite "in CI and on tenant enablement", and neither
    wants to parse a terminal layout."""
    assert main(["conformance", "--adapter", "fake", "--json"]) == 0
    signed = json.loads(capsys.readouterr().out)
    report = signed["report"]

    # The *signed* report, which is what S2.1.2 stores and Platform Health links. The
    # conformance result is nested inside it precisely so the hash covers the whole document.
    assert signed["content_hash"].startswith("sha256:")
    assert "signed" in signed
    assert report["passed"] is True
    assert report["adapter"] == "fake"
    assert report["interface_version"]
    assert len(report["checks"]) >= 6
    assert all(check["outcome"] in {"PASSED", "FAILED", "SKIPPED"} for check in report["checks"])


def test_asking_for_an_adapter_that_is_not_installed_says_what_is_true(capsys) -> None:
    """The honest answer names what is registered rather than failing obscurely.

    ``tableau`` was this test's example until F2.2 built it; ``looker`` stands in now, which
    is the more durable form — the message is what is under test.
    """
    exit_code = main(["conformance", "--adapter", "looker"])
    error = capsys.readouterr().err

    assert exit_code == 2, "not zero: nothing was verified"
    assert "no source adapter named 'looker'" in error
    assert "entry-point group" in error, "say how an adapter becomes findable"
    assert "fake" in error, "and what is registered"


def test_listing_shows_what_is_installed(capsys) -> None:
    assert main(["list"]) == 0
    assert "fake" in capsys.readouterr().out


def test_the_manifest_command_reports_capabilities(capsys) -> None:
    assert main(["manifest", "--adapter", "fake"]) == 0
    manifest = json.loads(capsys.readouterr().out)

    assert manifest["name"] == "fake"
    assert manifest["interface_version"]
    assert set(manifest["capabilities"]) == {
        "live_query",
        "extract_read",
        "usage",
        "ownership",
        "screenshot",
    }


@pytest.mark.process
def test_the_suite_runs_through_the_rpc(capsys) -> None:
    """``--remote``, which is the mode that matters for tenant enablement.

    §6.1 enables an adapter as a versioned worker image. A suite that only ever ran
    in-process would have certified something other than what is deployed.
    """
    exit_code = main(["conformance", "--adapter", "fake", "--remote"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "CONFORMANT" in output
    assert "adapter RPC" in output


# ----------------------------------------------------------------------- the registry


def test_an_adapter_registers_itself_through_an_entry_point() -> None:
    """§20: "A new source adapter is a repository that passes the harness."

    The SDK's own adapter is found the same way anyone else's will be, so the discovery path
    that finds `tableau` when F2.2 ships is exercised on every run today.
    """
    assert "fake" in registered_names()
    assert load_adapter("fake").manifest().name == "fake"


def test_an_adapter_can_be_registered_before_it_is_packaged() -> None:
    """An adapter under development is not installed yet, and having to package it to run
    the suite against it would make the suite the last thing an author reaches for rather
    than the first."""
    from astra_adapter.fake import build

    register("under-development", build)
    try:
        assert "under-development" in registered_names()
        assert load_adapter("under-development").manifest().name == "fake"
    finally:
        from astra_adapter import registry

        registry._REGISTERED.pop("under-development", None)


def test_an_unknown_adapter_names_what_is_available() -> None:
    """The usual cause is a package that is not on the path, and a bare "not found" sends
    people looking for a typo."""
    with pytest.raises(UnknownAdapter) as caught:
        load_adapter("looker")

    assert "fake" in str(caught.value)
    assert "entry-point group" in str(caught.value)
