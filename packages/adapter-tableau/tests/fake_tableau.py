"""The golden corpus, re-exported so the tests read as tests of the adapter.

It ships *with* the adapter (§6.3: an adapter ships with a corpus), so the tests use the
same deployment the conformance suite does rather than a second, similar one that could
drift from it.
"""

from __future__ import annotations

from astra_adapter_tableau.golden import (
    CLOUD_VERSION,
    REST_API_VERSION,
    SERVER_BUILD,
    SERVER_VERSION,
    FakeTableau,
    FakeWorkbook,
    credential_json,
    estate,
    twbx_bytes,
    workbook_xml,
)

__all__ = [
    "CLOUD_VERSION",
    "REST_API_VERSION",
    "SERVER_BUILD",
    "SERVER_VERSION",
    "FakeTableau",
    "FakeWorkbook",
    "credential_json",
    "estate",
    "twbx_bytes",
    "workbook_xml",
]
