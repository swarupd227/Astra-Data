"""The golden corpus: a Tableau deployment the adapter can be checked against.

    "An adapter ships with a corpus of source assets and expected graph fragments." — §6.3

For a source adapter the corpus has to include *a source*. A set of ``.twbx`` files on disk
is not enough to check discovery, paging, authentication, session expiry or throttling —
those live between the adapter and a server, and half of S2.2.1 is about exactly that.

So the golden corpus is a **deployment**: an ASGI app implementing the parts of Tableau's REST
and Metadata APIs this adapter uses, with the behaviours that make real integrations fail.

- Two deployment kinds, reporting different versions from ``/serverinfo``.
- Sessions that can be expired on demand, answering 401 the way a real one does.
- Both authentication kinds, with the rejections a wrong credential really produces.
- Pagination that must be followed, on both APIs.
- 429s with ``Retry-After``, on a schedule the caller sets.
- Real ``.twb`` and ``.twbx`` bytes — a genuine zip, with a genuine stand-in extract in it.

**It ships with the adapter rather than living in its tests**, because §6.3 makes the corpus
part of what an adapter *is*: `astra-tableau-golden` serves it, and the conformance suite runs
against it in CI. On tenant enablement it is replaced by a client-provided sample, which is a
real Tableau site — and the same suite, pointed elsewhere.

It is not a Tableau emulator. Where it is wrong it is wrong in the direction of being
stricter than Tableau, which is the safe direction for a double.
"""

from __future__ import annotations

import hashlib
import io
import json
import uuid
import zipfile
from dataclasses import dataclass, field
from typing import Any

import jwt
from PIL import Image, ImageDraw
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

#: The site the golden deployment serves, and the scope the shipped corpus names.
GOLDEN_SITE = "golden"

SERVER_VERSION = "2022.3.7"
SERVER_BUILD = "20223.22.1010.1416"
CLOUD_VERSION = "2026.1.0"
REST_API_VERSION = "3.19"


def workbook_xml(
    name: str,
    *,
    sheets: int = 3,
    published: bool = True,
    extract: bool = True,
    embedded_credentials: bool = True,
    custom_sql: bool = True,
    calculations: int = 2,
    unreadable_calculation: bool = False,
    row_level_security: bool = True,
    broken_custom_sql: bool = False,
) -> bytes:
    """A genuine Tableau workbook document, shaped like the ones that cause trouble.

    Not a realistic workbook — a realistic one is 40,000 lines and would make these tests
    about zip performance. But every element S2.2.2 turns on is real and in the places
    Tableau puts them:

    - a **published** datasource, marked by ``<repository-location>``, which is the only way
      the file distinguishes published from embedded;
    - an **embedded** one with a live connection and, by default, a ``password`` attribute —
      because real workbooks have those, and an adapter that has never met one has not been
      shown to strip it;
    - an **extract**, with the datasource columns it materialises;
    - **custom SQL**, which §4.1.1 requires be kept byte-for-byte;
    - **calculated fields**, which this story counts and does not parse.
    """
    # One sheet per filter kind §4.1.1's enum names, so S2.3.2's first criterion is exercised
    # against every branch rather than against the one that happened to be written first.
    filter_blocks = [
        # categorical
        """<filter class="categorical" column="[federated.p].[none:desk:nk]">
          <groupfilter function="member" level="[none:desk:nk]" member="&quot;Rates&quot;"/>
          <groupfilter function="member" level="[none:desk:nk]" member="&quot;Credit&quot;"/>
        </filter>""",
        # range (Tableau writes it as `quantitative`) and a relative date
        """<filter class="quantitative" column="[federated.p].[sum:notional:qk]"
                included-values="non-null">
          <min>1000</min><max>5000000</max>
        </filter>
        <filter class="relative-date" column="[federated.p].[none:trade_date:qk]"
                period-type="quarter" range-type="last-n" range-n="4" anchor="today"/>""",
        # top-N, which Tableau expresses as a categorical filter carrying a groupfilter
        """<filter class="categorical" column="[federated.p].[none:desk:nk]" context="true">
          <groupfilter function="end" count="10" direction="DESC"
                       expression="[federated.p].[sum:notional:qk]"/>
        </filter>""",
    ]

    def sheet_body(index: int) -> str:
        filters = filter_blocks[index % len(filter_blocks)]
        return f'''<worksheet name="{name} sheet {index}">
      <table>
        <view><datasources>
          <datasource caption="Positions" name="federated.positions"/>
        </datasources>
        {filters}
        </view>
        <rows>([federated.p].[none:desk:nk])</rows>
        <cols>([federated.p].[sum:notional:qk])</cols>
        <panes><pane><mark class="Bar"/>
          <encodings><color column="[federated.p].[none:book:nk]" attr="color"/></encodings>
        </pane></panes>
        <sort class="computed" column="[federated.p].[none:desk:nk]" direction="DESC"
              using="[federated.p].[sum:notional:qk]"/>
        <reference-line scope="per-pane" value="[federated.p].[avg:notional:qk]"
                        aggregation="avg"/>
      </table>
      <datasource-dependencies datasource="federated.positions"/>
    </worksheet>'''

    worksheets = "".join(sheet_body(index) for index in range(sheets))

    credentials = ' username="svc_reporting" password="hunter2"' if embedded_credentials else ""
    statement = (
        # A stored-procedure call: it may parse, and it still leaves the Modeller with a hole,
        # which is exactly the case S2.3.3 asks to be flagged rather than passed over.
        "EXEC sp_get_positions @desk = &apos;Rates&apos;"
        if broken_custom_sql
        else "select * from risk.positions where as_of_date &gt; current_date - 30"
    )
    relation = (
        '<relation name="Custom SQL Query" type="text">'
        f"{statement}"
        "<columns><column datatype='string' name='desk' role='dimension'/>"
        "<column datatype='real' name='notional' role='measure'/></columns>"
        "</relation>"
        if custom_sql
        else '<relation name="[risk].[positions]" type="table">'
        "<columns><column datatype='string' name='desk' role='dimension'/>"
        "<column datatype='real' name='notional' role='measure'/></columns>"
        "</relation>"
    )

    calculated = "".join(
        f"""<column caption="Calc {index}" datatype="real" name="[Calculation_{index}]" role="measure">
        <calculation class="tableau" formula="SUM([notional]) / COUNTD([desk]) + {index}"/>
      </column>"""
        for index in range(calculations)
    )
    if row_level_security:
        # A Tableau user filter, written the way Tableau writes one: a calculated field over
        # ISMEMBEROF, applied as an ordinary filter. Present by default because a workbook
        # restricting rows by viewer is common in a bank, and an adapter that has never met
        # one has not been shown to detect it.
        calculated += (
            '<column caption="Desk Access" datatype="boolean" name="[Desk Access]" '
            'role="dimension">'
            '<calculation class="tableau" '
            'formula="ISMEMBEROF(&apos;Rates Desk&apos;) OR USERNAME() = &apos;risk.admin&apos;"/>'
            "</column>"
        )

    if unreadable_calculation:
        # A construct outside the grammar. Not in the shipped corpus — S2.3.1 requires that to
        # parse at 100% — but a test needs one to check that it is retained verbatim, flagged,
        # and holds the workbook rather than being dropped or raised on.
        calculated += (
            '<column caption="Odd" datatype="real" name="[Odd]" role="measure">'
            '<calculation class="tableau" formula="MADE_UP_FUNCTION([notional], 3)"/>'
            "</column>"
        )

    # A nested zone tree, because flattening a layout to a list of rectangles loses the
    # containers the Compositor lays a Power BI page out from (§11.3).
    placements = "".join(
        f'<zone name="{name} sheet {index}" type-v2="worksheet" x="0" y="{index * 300}" '
        f'w="1000" h="300"/>'
        for index in range(sheets)
    )
    dashboard = (
        '<dashboard name="Overview">'
        '<size maxheight="900" maxwidth="1000"/>'
        f'<zones><zone type-v2="layout-flow" x="0" y="0" w="1000" h="900">{placements}</zone>'
        "</zones></dashboard>"
    )

    actions = (
        f'<action caption="Desk drill" name="a1" type="filter">'
        f'<source worksheet="{name} sheet 0"/><target worksheet="{name} sheet 1"/></action>'
        f'<action caption="Highlight book" name="a2" type="highlight">'
        f'<source worksheet="{name} sheet 0"/><target worksheet="{name} sheet 0"/></action>'
        f'<action caption="Open ticket" name="a3" type="url">'
        f'<source worksheet="{name} sheet 0"/></action>'
        f'<action caption="Set region" name="a4" type="change-parameter">'
        f'<source worksheet="{name} sheet 0"/><target worksheet="{name} sheet 1"/></action>'
        f'<action caption="Top desks" name="a5" type="change-set">'
        f'<source worksheet="{name} sheet 0"/><target worksheet="{name} sheet 1"/></action>'
    )

    published_block = (
        """<datasource caption="Reference Rates" name="reference.rates" inline="false">
      <repository-location id="RefRates" revision="1.3"/>
      <connection class="sqlserver" dbname="reference" server="sql-ref.internal" schema="dbo"
                  authentication="sspi">
        <relation name="[dbo].[fx_rates]" type="table">
          <columns><column datatype="string" name="ccy" role="dimension"/>
          <column datatype="real" name="rate" role="measure"/></columns>
        </relation>
      </connection>
      <column datatype="string" name="[ccy]" role="dimension"/>
      <column datatype="real" name="[rate]" role="measure"/>
    </datasource>"""
        if published
        else ""
    )

    extract_block = (
        """<extract refreshed-at="2026-08-30T02:14:00Z">
        <connection class="dataengine" dbname="Data/Extracts/federated.hyper"/>
      </extract>"""
        if extract
        else ""
    )

    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f'<workbook source-build="{SERVER_VERSION}" version="18.1" '
        'xmlns:user="http://www.tableausoftware.com/xml/user">\n'
        "  <datasources>\n"
        f"    {published_block}\n"
        f'    <datasource caption="Positions" name="federated.positions" inline="true">\n'
        f'      <connection class="postgres" dbname="risk" server="warehouse.internal" '
        f'schema="public" authentication="username-password"{credentials}>\n'
        f"        {relation}\n"
        "      </connection>\n"
        f"      {extract_block}\n"
        '      <column datatype="string" name="[desk]" role="dimension"/>\n'
        '      <column datatype="real" name="[notional]" role="measure" '
        'default-aggregation="Sum"/>\n'
        f"      {calculated}\n"
        "    </datasource>\n"
        '    <datasource caption="Parameters" name="Parameters">\n'
        '      <column datatype="integer" name="[Stress Factor]" role="measure"\n'
        '              param-domain-type="range" value="2"/>\n'
        '      <column datatype="string" name="[Region]" role="dimension"\n'
        '              param-domain-type="list" value="&quot;EMEA&quot;">\n'
        '        <aliases><alias key="&quot;EMEA&quot;" value="EMEA"/></aliases>\n'
        '        <members><member value="&quot;EMEA&quot;"/>\n'
        '        <member value="&quot;AMER&quot;"/></members>\n'
        "      </column>\n"
        "    </datasource>\n"
        "  </datasources>\n"
        f"  <worksheets>{worksheets}</worksheets>\n"
        f"  <dashboards>{dashboard}</dashboards>\n"
        f"  <actions>{actions}</actions>\n"
        "</workbook>\n"
    ).encode()


def twbx_bytes(
    name: str,
    *,
    with_extract: bool = True,
    declares_extract: bool | None = None,
    sheets: int = 3,
    published: bool = True,
    embedded_credentials: bool = True,
    calculations: int = 2,
    unreadable_calculation: bool = False,
    row_level_security: bool = True,
    broken_custom_sql: bool = False,
) -> bytes:
    """A real zip, containing a real .twb and — when asked — a stand-in extract.

    The extract matters: the adapter must record that it was there and never read it, and a
    test that packaged no extract could not tell the difference between "did not read it" and
    "there was nothing to read".
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            f"{name}.twb",
            workbook_xml(
                name,
                sheets=sheets,
                published=published,
                extract=with_extract if declares_extract is None else declares_extract,
                embedded_credentials=embedded_credentials,
                calculations=calculations,
                unreadable_calculation=unreadable_calculation,
                row_level_security=row_level_security,
                broken_custom_sql=broken_custom_sql,
            ),
        )
        archive.writestr("Image/shape.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
        if with_extract:
            archive.writestr(
                "Data/Extracts/federated.hyper",
                b"THIS IS CLIENT DATA AND MUST NOT BE READ" * 64,
            )
    return buffer.getvalue()


@dataclass
class FakeWorkbook:
    luid: str
    name: str
    project: str
    updated_at: str = "2026-08-01T09:00:00Z"
    revisions: int = 3
    packaged: bool = True
    owner: str = "risk.analyst@client.example"
    sheets: int = 3
    with_extract: bool = True
    published_datasource: bool = True
    embedded_credentials: bool = True
    calculations: int = 2
    unreadable_calculation: bool = False
    row_level_security: bool = True
    broken_custom_sql: bool = False

    def content(self) -> bytes:
        if self.packaged:
            return twbx_bytes(
                self.name,
                with_extract=self.with_extract,
                sheets=self.sheets,
                published=self.published_datasource,
                embedded_credentials=self.embedded_credentials,
                calculations=self.calculations,
                unreadable_calculation=self.unreadable_calculation,
                row_level_security=self.row_level_security,
                broken_custom_sql=self.broken_custom_sql,
            )
        return workbook_xml(
            self.name,
            sheets=self.sheets,
            published=self.published_datasource,
            extract=False,
            embedded_credentials=self.embedded_credentials,
            calculations=self.calculations,
            unreadable_calculation=self.unreadable_calculation,
            row_level_security=self.row_level_security,
            broken_custom_sql=self.broken_custom_sql,
        )


@dataclass
class FakeTableau:
    """The deployment. Everything a test wants to vary is a field."""

    site: str = "rqa"
    site_id: str = "site-0001"
    cloud: bool = False
    workbooks: list[FakeWorkbook] = field(default_factory=list)

    #: Credentials the deployment will accept.
    pat_name: str = "astra"
    pat_secret: str = "a-personal-access-token"
    connected_app_id: str = "app-0001"
    connected_app_secret_id: str = "secret-0001"
    connected_app_secret: str = "a-connected-app-secret"

    #: Behaviours a test turns on.
    throttle_next: int = 0
    """Answer this many of the next calls with 429."""

    retry_after: str | None = "1"
    metadata_enabled: bool = True
    view_data_delay: float = 0.0
    """Seconds the view-data endpoint stalls before answering. For the timeout path."""

    extract_refresh_tasks: bool = True
    """Whether this credential may read the site's extract-refresh tasks. Off models the
    common content-reader case, where Tableau answers 403."""

    product_version: str | None = None
    """Override what /serverinfo reports. A field rather than something a caller patches:
    the 2021.4 floor is a real behaviour and testing it should not require reaching into
    another module's globals."""
    revision_history: bool = True
    page_size_cap: int = 2
    """Deliberately small, so paging is exercised rather than accidentally skipped."""

    #: Observed, for assertions.
    calls: list[str] = field(default_factory=list)
    sign_ins: int = 0
    downloads: list[str] = field(default_factory=list)
    tokens: set[str] = field(default_factory=set)
    expired: set[str] = field(default_factory=set)

    def expire_sessions(self) -> None:
        """End every live session, the way a Tableau restart or a timeout does."""
        self.expired |= self.tokens
        self.tokens = set()

    # ----------------------------------------------------------------- the app

    def app(self) -> Starlette:
        return Starlette(
            routes=[
                Route("/api/{version}/serverinfo", self.server_info, methods=["GET"]),
                Route("/api/{version}/auth/signin", self.sign_in, methods=["POST"]),
                Route("/api/{version}/auth/signout", self.sign_out, methods=["POST"]),
                Route("/api/{version}/sites", self.sites, methods=["GET"]),
                Route(
                    "/api/{version}/sites/{site_id}/workbooks",
                    self.list_workbooks,
                    methods=["GET"],
                ),
                Route(
                    "/api/{version}/sites/{site_id}/workbooks/{luid}/revisions",
                    self.list_revisions,
                    methods=["GET"],
                ),
                Route(
                    "/api/{version}/sites/{site_id}/workbooks/{luid}/content",
                    self.download,
                    methods=["GET"],
                ),
                Route(
                    "/api/{version}/sites/{site_id}/tasks/extractRefreshes",
                    self.extract_refreshes,
                    methods=["GET"],
                ),
                Route(
                    "/api/{version}/sites/{site_id}/workbooks/{luid}/views",
                    self.list_views,
                    methods=["GET"],
                ),
                Route(
                    "/api/{version}/sites/{site_id}/views/{view_id}/data",
                    self.view_data,
                    methods=["GET"],
                ),
                Route(
                    "/api/{version}/sites/{site_id}/views/{view_id}/image",
                    self.view_image,
                    methods=["GET"],
                ),
                Route("/api/metadata/graphql", self.graphql, methods=["POST"]),
            ]
        )

    # ------------------------------------------------------------------ helpers

    def _throttled(self) -> Response | None:
        if self.throttle_next <= 0:
            return None
        self.throttle_next -= 1
        headers = {"Retry-After": self.retry_after} if self.retry_after else {}
        return JSONResponse(
            {
                "error": {
                    "summary": "Rate limit exceeded",
                    "detail": "Too many requests to this site",
                    "code": "429000",
                }
            },
            status_code=429,
            headers=headers,
        )

    def _authenticated(self, request: Request) -> Response | None:
        token = request.headers.get("X-Tableau-Auth", "")
        if token in self.tokens:
            return None
        return JSONResponse(
            {
                "error": {
                    "summary": "Unauthorized Access",
                    "detail": "Invalid authentication credentials were provided",
                    "code": "401002",
                }
            },
            status_code=401,
        )

    def _guard(self, request: Request, label: str) -> Response | None:
        self.calls.append(label)
        return self._throttled() or self._authenticated(request)

    # ------------------------------------------------------------------- routes

    async def server_info(self, request: Request) -> Response:
        self.calls.append("serverinfo")
        if throttled := self._throttled():
            return throttled
        return JSONResponse(
            {
                "serverInfo": {
                    "productVersion": {
                        "value": self.product_version
                        or (CLOUD_VERSION if self.cloud else SERVER_VERSION),
                        "build": "" if self.cloud else SERVER_BUILD,
                    },
                    "restApiVersion": REST_API_VERSION,
                }
            }
        )

    async def sign_in(self, request: Request) -> Response:
        self.calls.append("signin")
        if throttled := self._throttled():
            return throttled

        body = await request.json()
        credentials = (body or {}).get("credentials") or {}
        site = (credentials.get("site") or {}).get("contentUrl", "")

        if site != self.site:
            return self._rejected(f"site {site!r} does not exist on this server")

        if "jwt" in credentials:
            failure = self._check_jwt(str(credentials["jwt"]))
            if failure is not None:
                return failure
        else:
            name = credentials.get("personalAccessTokenName")
            secret = credentials.get("personalAccessTokenSecret")
            if name != self.pat_name or secret != self.pat_secret:
                return self._rejected("the personal access token is not valid")

        self.sign_ins += 1
        token = f"token-{uuid.uuid4()}"
        self.tokens.add(token)
        return JSONResponse(
            {
                "credentials": {
                    "token": token,
                    "site": {"id": self.site_id, "contentUrl": self.site},
                    "user": {"id": "user-0001"},
                }
            }
        )

    def _check_jwt(self, token: str) -> Response | None:
        try:
            header = jwt.get_unverified_header(token)
            claims = jwt.decode(
                token, self.connected_app_secret, algorithms=["HS256"], audience="tableau"
            )
        except jwt.PyJWTError as exc:
            return self._rejected(f"the connected-app token is not valid: {exc}")

        if header.get("kid") != self.connected_app_secret_id:
            return self._rejected("the connected-app token names an unknown secret id")
        if claims.get("iss") != self.connected_app_id:
            return self._rejected("the connected-app token names an unknown client id")
        if not claims.get("sub"):
            return self._rejected("the connected-app token names no user")
        return None

    def _rejected(self, detail: str) -> Response:
        return JSONResponse(
            {"error": {"summary": "Signin Error", "detail": detail, "code": "401001"}},
            status_code=401,
        )

    async def sign_out(self, request: Request) -> Response:
        self.calls.append("signout")
        self.tokens.discard(request.headers.get("X-Tableau-Auth", ""))
        return Response(status_code=204)

    async def sites(self, request: Request) -> Response:
        if refused := self._guard(request, "sites"):
            return refused
        return JSONResponse(
            {
                "sites": {
                    "site": [
                        {
                            "id": self.site_id,
                            "contentUrl": self.site,
                            "name": self.site.upper() or "Default",
                            "state": "Active",
                        }
                    ]
                }
            }
        )

    async def list_workbooks(self, request: Request) -> Response:
        if refused := self._guard(request, "workbooks"):
            return refused

        page = int(request.query_params.get("pageNumber", 1))
        size = min(int(request.query_params.get("pageSize", 100)), self.page_size_cap)
        start = (page - 1) * size
        window = self.workbooks[start : start + size]

        return JSONResponse(
            {
                "pagination": {
                    "pageNumber": str(page),
                    "pageSize": str(size),
                    "totalAvailable": str(len(self.workbooks)),
                },
                "workbooks": {
                    "workbook": [
                        {
                            "id": item.luid,
                            "name": item.name,
                            "contentUrl": item.name.replace(" ", ""),
                            "updatedAt": item.updated_at,
                            "size": 2,
                            "hasExtracts": "true" if item.with_extract else "false",
                            "project": {"id": f"proj-{item.project}", "name": item.project},
                            "owner": {"id": "user-0001"},
                        }
                        for item in window
                    ]
                },
            }
        )

    async def list_revisions(self, request: Request) -> Response:
        if refused := self._guard(request, "revisions"):
            return refused
        if not self.revision_history:
            return JSONResponse(
                {
                    "error": {
                        "summary": "Resource Not Found",
                        "detail": "Revision history is not enabled on this site",
                        "code": "404019",
                    }
                },
                status_code=404,
            )
        luid = request.path_params["luid"]
        item = self._workbook(luid)
        if item is None:
            return JSONResponse(
                {"error": {"summary": "Not Found", "detail": luid, "code": "404006"}},
                status_code=404,
            )
        return JSONResponse(
            {
                "revisions": {
                    "revision": [
                        {"revisionNumber": str(number), "publishedAt": item.updated_at}
                        # Deliberately not in order: the adapter must sort rather than assume.
                        for number in reversed(range(1, item.revisions + 1))
                    ][::-1]
                }
            }
        )

    async def download(self, request: Request) -> Response:
        if refused := self._guard(request, "download"):
            return refused
        luid = request.path_params["luid"]
        item = self._workbook(luid)
        if item is None:
            return JSONResponse(
                {"error": {"summary": "Not Found", "detail": luid, "code": "404006"}},
                status_code=404,
            )
        self.downloads.append(luid)
        include = request.query_params.get("includeExtract", "false").lower() == "true"
        payload = item.content()
        if item.packaged and not include:
            # Tableau honours includeExtract=false by repackaging without the extract. The
            # fake does too, so a test that asserts "no extract was read" is asserting about
            # the adapter and not about a fake that never sent one.
            # Tableau honours includeExtract=false by repackaging **without the .hyper
            # file** — and keeping the `<extract>` element that describes it. That asymmetry
            # is the whole mechanism: it is how an adapter detects an extract and records its
            # schema without ever downloading a row of client data. A fake that dropped the
            # element too would have hidden the behaviour under test.
            payload = twbx_bytes(
                item.name,
                with_extract=False,
                declares_extract=True,
                sheets=item.sheets,
                published=item.published_datasource,
                embedded_credentials=item.embedded_credentials,
                calculations=item.calculations,
                unreadable_calculation=item.unreadable_calculation,
                row_level_security=item.row_level_security,
                broken_custom_sql=item.broken_custom_sql,
            )
        return Response(
            payload,
            media_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{item.name}.twbx"'},
        )

    async def extract_refreshes(self, request: Request) -> Response:
        if refused := self._guard(request, "extractRefreshes"):
            return refused
        if not self.extract_refresh_tasks:
            # A content-reader credential cannot see these on many deployments, and Tableau
            # answers 403 rather than an empty list.
            return JSONResponse(
                {
                    "error": {
                        "summary": "Forbidden",
                        "detail": "Site administrator access is required",
                        "code": "403004",
                    }
                },
                status_code=403,
            )
        return JSONResponse(
            {
                "tasks": {
                    "task": [
                        {
                            "extractRefresh": {
                                "id": "task-1",
                                "type": "FullRefresh",
                                "datasource": {"id": "ds-1", "name": "Positions"},
                                "schedule": {
                                    "id": "sched-1",
                                    "name": "Nightly",
                                    "frequency": "Daily",
                                    "frequencyDetails": {
                                        "start": "02:00:00",
                                        "intervals": {"interval": [{"hours": "24"}]},
                                    },
                                },
                            }
                        }
                    ]
                }
            }
        )

    async def list_views(self, request: Request) -> Response:
        if refused := self._guard(request, "views"):
            return refused
        item = self._workbook(request.path_params["luid"])
        if item is None:
            return JSONResponse(
                {"error": {"summary": "Not Found", "detail": "workbook", "code": "404006"}},
                status_code=404,
            )
        return JSONResponse(
            {
                "views": {
                    "view": [
                        {"id": f"{item.luid}-view-{index}", "name": f"{item.name} sheet {index}"}
                        for index in range(item.sheets)
                    ]
                    # Tableau's views listing does not distinguish a sheet from a dashboard —
                    # both are published views, which is why `views.resolve_view_id` needs
                    # only one lookup for S2.4.1's ParityCase.sheet and S2.4.2's
                    # VisualCase.view_name alike.
                    + [{"id": f"{item.luid}-view-dashboard", "name": "Overview"}]
                }
            }
        )

    async def view_data(self, request: Request) -> Response:
        """Tableau's view-data endpoint: CSV, with `vf_` filters applied.

        The **nulls** are the part worth being faithful about. Tableau writes an empty field
        for a null and the literal ``%null%`` for a null aggregate, and a fake that returned
        neither could not show that the adapter preserves them — which is S2.4.1's third
        criterion and the thing §4.4's null policy rests on.
        """
        if refused := self._guard(request, "viewdata"):
            return refused

        if self.view_data_delay:
            # A slow view, for the timeout path. §10.2 makes a timeout INCONCLUSIVE rather
            # than FAIL, and a suite that never met a slow one could not show it.
            import asyncio

            await asyncio.sleep(self.view_data_delay)

        applied = {
            key[3:]: value for key, value in request.query_params.items() if key.startswith("vf_")
        }
        desks = ["Rates", "Credit", "FX"]
        if "desk" in applied:
            desks = [desk for desk in desks if desk == applied["desk"]]

        lines = ["Desk,Amount"]
        for index, desk in enumerate(desks):
            # One genuine null, and Tableau's own two spellings of it across the estate.
            amount = "" if index == 0 else ("%null%" if index == 2 else f"{(index + 1) * 1000}")
            lines.append(f"{desk},{amount}")
        return Response("\n".join(lines), media_type="text/csv")

    async def view_image(self, request: Request) -> Response:
        """Tableau's queryViewImage endpoint: a real, decodable PNG.

        Not a rendering of the workbook — this fake has no visual engine — but a genuine
        image nonetheless, at a **native size independent of anything the caller asked for**,
        because that asymmetry is exactly what S2.4.2's adapter-side resize exists to close.
        A fake that already returned the requested size would certify nothing about the
        resize path; the golden deployment ignoring the caller's wishes is what makes the
        adapter's own resizing the thing under test.
        """
        if refused := self._guard(request, "image"):
            return refused
        view_id = request.path_params["view_id"]
        found = self._view(view_id)
        if found is None:
            return JSONResponse(
                {"error": {"summary": "Not Found", "detail": "view", "code": "404006"}},
                status_code=404,
            )
        _, view_name = found
        # The parameters reach the render, not just the request: a fake that ignored them
        # could not show that S2.4.2's `vf_` parameters actually left the adapter.
        applied = str(request.url.query)
        return Response(
            _render_view_image(view_id, view_name, applied), media_type="image/png"
        )

    async def graphql(self, request: Request) -> Response:
        if refused := self._guard(request, "graphql"):
            return refused
        if not self.metadata_enabled:
            return JSONResponse(
                {"errors": [{"message": "The Metadata API is not enabled on this server"}]}
            )

        body = await request.json()
        variables = (body or {}).get("variables") or {}
        first = min(int(variables.get("first", self.page_size_cap)), self.page_size_cap)
        offset = int(variables.get("offset", 0))
        window = self.workbooks[offset : offset + first]

        return JSONResponse(
            {
                "data": {
                    "workbooksConnection": {
                        "totalCount": len(self.workbooks),
                        "pageInfo": {
                            "hasNextPage": offset + first < len(self.workbooks),
                            "endCursor": str(offset + first),
                        },
                        "nodes": [
                            {
                                "id": f"meta-{item.luid}",
                                "luid": item.luid,
                                "name": item.name,
                                "projectName": item.project,
                                "projectLuid": f"proj-{item.project}",
                                "createdAt": "2025-01-01T00:00:00Z",
                                "updatedAt": item.updated_at,
                                "uri": f"workbooks/{item.luid}",
                                "owner": {
                                    "username": item.owner,
                                    "name": item.owner,
                                    "email": item.owner,
                                },
                                "containerName": "Risk",
                                "sheets": [
                                    {"id": f"s{n}", "name": f"{item.name} sheet {n}"}
                                    for n in range(item.sheets)
                                ],
                                "dashboards": [{"id": "d0", "name": "Overview"}],
                                "embeddedDatasources": [
                                    {
                                        "id": "ds0",
                                        "name": "federated.positions",
                                        "hasExtracts": item.with_extract,
                                        "extractLastRefreshTime": "2026-08-30T02:14:00Z",
                                        "extractLastIncrementalUpdateTime": None,
                                    }
                                ],
                                "upstreamDatasources": (
                                    [
                                        {
                                            "id": "pds0",
                                            "name": "reference.rates",
                                            "luid": "ds-refrates-0001",
                                            "hasExtracts": False,
                                            "extractLastRefreshTime": None,
                                            "projectName": "Reference",
                                        }
                                    ]
                                    if item.published_datasource
                                    else []
                                ),
                            }
                            for item in window
                        ],
                    }
                }
            }
        )

    def _workbook(self, luid: str) -> FakeWorkbook | None:
        return next((item for item in self.workbooks if item.luid == luid), None)

    def _view(self, view_id: str) -> tuple[FakeWorkbook, str] | None:
        """The workbook and view name behind a view id — the same ids `list_views` hands out."""
        for item in self.workbooks:
            for index in range(item.sheets):
                if view_id == f"{item.luid}-view-{index}":
                    return item, f"{item.name} sheet {index}"
            if view_id == f"{item.luid}-view-dashboard":
                return item, "Overview"
        return None


#: A native size independent of what any caller asks for (see `view_image`'s docstring).
_NATIVE_VIEW_SIZE = (960, 720)


def _render_view_image(view_id: str, view_name: str, query: str = "") -> bytes:
    """A real PNG, distinct per view and per parameter set, at a size the caller did not
    choose.

    The colour is a hash of the view id **and the query string**, so two different views, or
    the same view with different `vf_` parameters applied, produce two different images — a
    fake that always drew the same picture could not show that the adapter fetched *this*
    view under *these* parameters rather than some other combination.
    """
    digest = hashlib.sha256(f"{view_id}|{query}".encode()).digest()
    colour = (digest[0], digest[1], digest[2])

    image = Image.new("RGB", _NATIVE_VIEW_SIZE, color=colour)
    draw = ImageDraw.Draw(image)
    draw.rectangle(
        [40, 40, _NATIVE_VIEW_SIZE[0] - 40, _NATIVE_VIEW_SIZE[1] - 40],
        outline=(255, 255, 255),
        width=4,
    )
    draw.text((60, 60), view_name, fill=(255, 255, 255))

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def estate(count: int = 5, *, site: str = GOLDEN_SITE) -> FakeTableau:
    """A small estate across two projects, with one unpackaged workbook.

    The unpackaged one matters: S2.2.1 requires both `.twb` and `.twbx`, and an estate of
    only one kind would leave half the criterion untested.
    """
    workbooks = [
        FakeWorkbook(
            luid=f"wb-{index:05d}",
            name=f"Workbook {index}",
            project="Risk" if index % 2 == 0 else "Trading",
            packaged=index != 1,
            revisions=1 + (index % 3),
            sheets=2 + (index % 2),
        )
        for index in range(count)
    ]
    return FakeTableau(site=site, workbooks=workbooks)


def credential_json(kind: str = "personal_access_token", **overrides: Any) -> str:
    base: dict[str, Any] = (
        {
            "kind": "personal_access_token",
            "token_name": "astra",
            "secret": "a-personal-access-token",
        }
        if kind == "personal_access_token"
        else {
            "kind": "connected_app",
            "client_id": "app-0001",
            "secret_id": "secret-0001",
            "secret": "a-connected-app-secret",
            "username": "svc.astra@client.example",
        }
    )
    base.update(overrides)
    return json.dumps(base)
