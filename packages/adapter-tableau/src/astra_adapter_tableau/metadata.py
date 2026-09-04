"""The Tableau Metadata API — the object graph, in one query per page.

    "Discovery uses the Metadata API (GraphQL) for the object graph" — S2.2.1

§6.2 names what to ask for: ``workbooks { sheets { upstreamFields, upstreamTables } }``,
``calculatedFields { formula, upstreamFields }``, ``publishedDatasources``,
``databaseTables``, ``parameters``.

**Why this and not 1,067 REST calls.** The REST API can list workbooks and can list a
workbook's views, and nothing else about the shape of one. Building the object graph from it
means a call per workbook per relationship — tens of thousands of round trips against a rate
limit, for an estate the Metadata API describes in a handful of pages.

**Paging is not optional.** Tableau's GraphQL endpoint has no server-side result cap it will
tell you about; it simply times out on a query it considers too large, and a timeout looks
exactly like a network problem. `offset`/`first` paging keeps each query small enough to
answer, and the page size is deliberately below what the endpoint will accept.

**What this story uses it for.** Discovery: which workbooks exist, in which projects, with
which owners and revisions. The sheets, fields and upstream tables that §6.2 lists are
*parsed from the downloaded workbook* by F2.3, not read from here — the Metadata API's view
of a calculation is its formula as text, and the platform needs the AST. This module asks for
the shape of the estate; the fetch brings back the thing itself.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from astra_adapter import AdapterError

from .rest import TableauRestClient

logger = logging.getLogger(__name__)

#: The Metadata API's single endpoint. Unversioned — Tableau evolves the schema additively.
METADATA_PATH = "/api/metadata/graphql"

#: Discovery. Deliberately the *shallow* shape: everything here is needed to decide what to
#: fetch, and nothing here is needed to parse what was fetched.
#:
#: ``luid`` rather than the Metadata API's own ``id``: the REST API downloads by LUID, the
#: two identifiers are different, and using the wrong one produces a 404 that reads like a
#: missing workbook.
DISCOVERY_QUERY = """
query Discovery($first: Int!, $offset: Int!) {
  workbooksConnection(first: $first, offset: $offset) {
    totalCount
    pageInfo { hasNextPage endCursor }
    nodes {
      id
      luid
      name
      projectName
      projectLuid
      createdAt
      updatedAt
      uri
      owner { username name email }
      containerName
      sheets { id name }
      dashboards { id name }
      embeddedDatasources {
        id
        name
        hasExtracts
        extractLastRefreshTime
        extractLastIncrementalUpdateTime
      }
      upstreamDatasources {
        id
        name
        luid
        hasExtracts
        extractLastRefreshTime
        projectName
      }
    }
  }
}
"""


@dataclass(frozen=True, slots=True)
class MetadataWorkbook:
    """A workbook as the Metadata API describes it, before anything is downloaded."""

    luid: str
    metadata_id: str
    name: str
    project_name: str
    project_luid: str
    owner_username: str
    owner_email: str
    updated_at: str
    created_at: str
    sheet_count: int = 0
    dashboard_count: int = 0
    embedded_datasources: tuple[str, ...] = ()
    published_datasources: tuple[str, ...] = ()
    has_extracts: bool = False

    published_datasource_luids: tuple[tuple[str, str], ...] = ()
    """Published datasource name → LUID (S2.2.2).

    Pairs rather than a dict so the record stays hashable and comparable, which is what makes
    two discoveries of an unchanged estate compare equal.

    The *file* also records a published datasource's id, and the two disagree often enough —
    a datasource republished under a new name, a workbook downloaded before a rename — that
    the fragment builder prefers the file and falls back to this."""

    extract_refreshes: tuple[tuple[str, str], ...] = ()
    """Datasource name → last extract refresh (S2.2.2's "refresh schedule").

    Tableau's Metadata API reports *when it last refreshed*, not the schedule that governs
    it; the schedule itself is a REST concept and lives on the site's extract-refresh tasks.
    Both are recorded, and they are different facts: "refreshes nightly at 02:00" and "last
    refreshed nine weeks ago" tell a migration programme very different things, and the
    second is the one that reveals an abandoned report."""

    def as_properties(self) -> dict[str, Any]:
        return {
            "metadata_id": self.metadata_id,
            "project": self.project_name,
            "sheets": self.sheet_count,
            "dashboards": self.dashboard_count,
            "embedded_datasources": list(self.embedded_datasources),
            "published_datasources": list(self.published_datasources),
            "has_extracts": self.has_extracts,
            "extract_last_refresh": dict(self.extract_refreshes),
        }


@dataclass(slots=True)
class MetadataAvailability:
    """Whether the Metadata API is on, which it very often is not.

    §6.2 marks it as the source of lineage and usage; a Tableau Server administrator can
    disable the Metadata API entirely, and many have. That is a fact about the deployment —
    exactly what `Capabilities` exists to report (§6.1) — and the Estate surface is meant to
    show it rather than show an estate with no lineage and no explanation.
    """

    available: bool
    detail: str = ""
    _reason: str = field(default="", repr=False)

    def as_dict(self) -> dict[str, Any]:
        return {"metadata_api": self.available, "detail": self.detail}


class TableauMetadataClient:
    """GraphQL over the REST client's session, so both share one throttle and one sign-in."""

    def __init__(self, rest: TableauRestClient, *, page_size: int = 200) -> None:
        self._rest = rest
        self._page_size = page_size
        self._availability: MetadataAvailability | None = None

    async def availability(self) -> MetadataAvailability:
        """Probe with the cheapest possible query.

        A one-row query rather than a schema introspection: introspection can be permitted
        on a deployment where the data is not, so it would answer a question nobody asked.
        """
        if self._availability is not None:
            return self._availability

        try:
            await self._query("query Probe { workbooksConnection(first: 1) { totalCount } }")
        except AdapterError as exc:
            self._availability = MetadataAvailability(
                available=False,
                detail=(
                    f"the Metadata API did not answer on this deployment: {exc}. It can be "
                    f"disabled by a Tableau Server administrator; discovery falls back to the "
                    f"REST listing, which knows workbooks and projects but not lineage."
                ),
            )
            logger.warning("Metadata API unavailable: %s", exc)
        else:
            self._availability = MetadataAvailability(available=True, detail="")
        return self._availability

    async def workbooks(self) -> list[MetadataWorkbook]:
        """Every workbook on the site, paged."""
        found: list[MetadataWorkbook] = []
        offset = 0
        while True:
            body = await self._query(DISCOVERY_QUERY, {"first": self._page_size, "offset": offset})
            connection = (body.get("workbooksConnection") or {}) if body else {}
            nodes = connection.get("nodes") or []
            found.extend(_workbook(node) for node in nodes)

            total = int(connection.get("totalCount") or len(found))
            if not nodes or len(found) >= total:
                logger.info("metadata discovery returned %d workbooks", len(found))
                return found
            offset += len(nodes)

    async def _query(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        response = await self._rest.call(
            "POST", METADATA_PATH, json_body={"query": query, "variables": variables or {}}
        )
        body = response.json()
        if not isinstance(body, dict):
            raise AdapterError("the Metadata API returned something that is not an object")

        # GraphQL answers 200 with an ``errors`` array, so a status check alone would read a
        # failed query as a successful one and return an empty estate.
        if errors := body.get("errors"):
            messages = "; ".join(str(item.get("message", item)) for item in errors[:3])
            raise AdapterError(f"the Metadata API rejected the query: {messages}", retryable=False)
        data = body.get("data")
        if data is None:
            raise AdapterError("the Metadata API returned no data and no errors")
        return dict(data)


def _workbook(node: dict[str, Any]) -> MetadataWorkbook:
    owner = node.get("owner") or {}
    embedded = node.get("embeddedDatasources") or []
    published = node.get("upstreamDatasources") or []
    return MetadataWorkbook(
        luid=str(node.get("luid") or ""),
        metadata_id=str(node.get("id") or ""),
        name=str(node.get("name") or ""),
        project_name=str(node.get("projectName") or ""),
        project_luid=str(node.get("projectLuid") or ""),
        owner_username=str(owner.get("username") or owner.get("name") or ""),
        owner_email=str(owner.get("email") or ""),
        updated_at=str(node.get("updatedAt") or ""),
        created_at=str(node.get("createdAt") or ""),
        sheet_count=len(node.get("sheets") or []),
        dashboard_count=len(node.get("dashboards") or []),
        embedded_datasources=tuple(str(item.get("name", "")) for item in embedded),
        published_datasources=tuple(str(item.get("name", "")) for item in published),
        has_extracts=any(bool(item.get("hasExtracts")) for item in [*embedded, *published]),
        published_datasource_luids=tuple(
            (str(item.get("name", "")), str(item.get("luid", "")))
            for item in published
            if item.get("name") and item.get("luid")
        ),
        extract_refreshes=tuple(
            (str(item.get("name", "")), str(refreshed))
            for item in [*embedded, *published]
            if item.get("name")
            and (
                refreshed := item.get("extractLastRefreshTime")
                or item.get("extractLastIncrementalUpdateTime")
            )
        ),
    )
