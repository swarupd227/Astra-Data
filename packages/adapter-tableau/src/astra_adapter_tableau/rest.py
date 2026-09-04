"""The Tableau REST API client — sign-in, server info, downloads, revisions.

    "Discovery uses the Metadata API (GraphQL) for the object graph and the REST API for
    downloads" — S2.2.1

The division is Tableau's, not ours. The Metadata API knows the object graph and cannot hand
you a file; the REST API downloads files and describes the estate only shallowly. Using each
for what it is good at is why discovery is one query and not 1,067.

**Server and Cloud are the same API.** They differ in which version is available and how
quickly they throttle, and both are read from the deployment rather than configured — see
`server_info`.

**Every response goes through one place.** `_call` holds the session, the concurrency slot,
the 429 backoff and the 401 re-sign-in, because those four interact: a 401 during a backoff
must not re-sign-in twice, and a re-sign-in must not consume a retry budget meant for
throttling.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any
from xml.etree import ElementTree

import httpx
from astra_adapter import AdapterError, RateLimited

from .auth import Session, authentication_failure, session_from_response, sign_in_payload
from .config import MINIMUM_API_VERSION, SERVER_FLOOR, TableauConfig
from .throttle import SiteThrottle, retry_after_seconds

logger = logging.getLogger(__name__)

#: Tableau's REST responses are XML by default and JSON when asked. JSON is asked for.
JSON_HEADERS = {"Accept": "application/json", "Content-Type": "application/json"}

#: Server product versions look like ``2021.4.5``; Cloud reports the same field with a
#: date-like value that has no meaningful comparison to a Server release.
_SERVER_VERSION = re.compile(r"^(\d{4})\.(\d+)")


class Deployment(str, Enum):
    """Which of the two the story names this is.

    Detected rather than configured. An operator should not have to tell the platform whether
    their Tableau is Server or Cloud — the deployment says so, and asking is an opportunity to
    be told the wrong thing.
    """

    SERVER = "server"
    CLOUD = "cloud"


@dataclass(frozen=True, slots=True)
class ServerInfo:
    """What the deployment says about itself (S2.2.1's fourth criterion)."""

    product_version: str
    build: str
    rest_api_version: str
    deployment: Deployment

    @property
    def supported(self) -> bool:
        """Server 2021.4+ or any Cloud.

        Cloud is continuously updated and always at or beyond the floor, so there is nothing
        to compare; comparing its version string to a Server release would be comparing two
        different numbering schemes and getting a confident wrong answer.
        """
        if self.deployment is Deployment.CLOUD:
            return True
        match = _SERVER_VERSION.match(self.product_version)
        if match is None:
            return False
        year, quarter = int(match.group(1)), int(match.group(2))
        floor_year, floor_quarter = (int(part) for part in SERVER_FLOOR.split("."))
        return (year, quarter) >= (floor_year, floor_quarter)

    def as_dict(self) -> dict[str, Any]:
        return {
            "product_version": self.product_version,
            "build": self.build,
            "rest_api_version": self.rest_api_version,
            "deployment": self.deployment.value,
            "supported": self.supported,
        }


@dataclass(frozen=True, slots=True)
class WorkbookRef:
    """A workbook as the REST API describes it. The Metadata API supplies the rest."""

    id: str
    name: str
    content_url: str
    project_id: str
    project_name: str
    owner_id: str
    updated_at: str
    size_mb: int = 0
    has_extracts: bool = False


class TableauRestClient:
    """One authenticated conversation with one Tableau deployment."""

    def __init__(
        self,
        config: TableauConfig,
        *,
        throttle: SiteThrottle,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._config = config
        self._throttle = throttle
        self._client = client or httpx.AsyncClient(
            base_url=config.base_url,
            timeout=config.request_timeout,
            verify=config.verify_tls,
            follow_redirects=True,
        )
        self._session: Session | None = None
        self._info: ServerInfo | None = None
        if not config.verify_tls:
            logger.warning(
                "TLS verification is disabled for %s — acceptable for a self-signed staging "
                "server, never for a client tenant",
                config.base_url,
            )

    # ------------------------------------------------------------------ lifecycle

    async def aclose(self) -> None:
        if self._session is not None:
            await self._sign_out()
        await self._client.aclose()

    @property
    def session(self) -> Session | None:
        return self._session

    @property
    def info(self) -> ServerInfo | None:
        return self._info

    # -------------------------------------------------------------- server info

    async def server_info(self) -> ServerInfo:
        """Ask the deployment what it is (S2.2.1: "the version is recorded per site").

        Unauthenticated, and deliberately the *first* call: the API version every later call
        uses comes from here, and a deployment below the 2021.4 floor should be reported as
        unsupported before a credential is presented to it.
        """
        if self._info is not None:
            return self._info

        path = f"/api/{MINIMUM_API_VERSION}/serverinfo"

        async def send(_: Session | None, url: str) -> httpx.Response:
            return await self._client.get(url, headers={"Accept": "application/json"})

        # Backed off like every other call, and it is worth saying why this needed saying.
        # An earlier version had no 429 handling here — it is the *first* call and the one
        # least likely to be throttled — and so a rate-limited deployment was reported as
        # "below the 2021.4 floor". A wrong diagnosis on the first call sends whoever reads
        # it to check a Tableau version that was never the problem.
        response = await self._with_backoff("GET", path, send, authenticated=False)
        if response.status_code >= 400:
            raise AdapterError(
                f"{self._config.base_url} did not answer {path} "
                f"({response.status_code}). Tableau Server {SERVER_FLOOR} and later do; an "
                f"older one does not, and is outside what this adapter supports.",
                retryable=response.status_code >= 500,
            )

        body = _json(response).get("serverInfo") or {}
        product = str((body.get("productVersion") or {}).get("value", ""))
        build = str((body.get("productVersion") or {}).get("build", ""))
        rest_version = str(body.get("restApiVersion") or MINIMUM_API_VERSION)

        info = ServerInfo(
            product_version=product,
            build=build,
            rest_api_version=rest_version,
            deployment=_deployment_of(self._config.base_url, product),
        )
        if not info.supported:
            raise AdapterError(
                f"Tableau Server {product or 'of an unreported version'} is below the "
                f"{SERVER_FLOOR} floor this adapter supports (S2.2.1). Harvesting it would "
                f"produce an estate whose gaps nobody could account for.",
                retryable=False,
            )
        self._info = info
        logger.info(
            "connected to Tableau %s %s (REST API %s)",
            info.deployment.value,
            product or "cloud",
            rest_version,
        )
        return info

    # ------------------------------------------------------------------- signing

    async def sign_in(self) -> Session:
        if self._session is not None and self._session.is_fresh():
            return self._session

        credential = self._config.credential
        if credential is None:
            raise AdapterError(
                "no Tableau credential is configured for this adapter worker; set "
                "ASTRA_TABLEAU_CREDENTIAL",
                retryable=False,
            )

        info = await self.server_info()
        version = info.rest_api_version

        try:
            response = await self._client.post(
                f"/api/{version}/auth/signin",
                json=sign_in_payload(credential, self._config.site),
                headers=JSON_HEADERS,
            )
        except httpx.HTTPError as exc:
            raise self._transport_failure("POST", f"/api/{version}/auth/signin", exc) from exc
        if response.status_code in (401, 403):
            raise authentication_failure(credential, _error_detail(response))
        if response.status_code >= 400:
            raise AdapterError(
                f"Tableau refused the sign-in ({response.status_code}): {_error_detail(response)}",
                retryable=response.status_code >= 500,
            )

        self._session = session_from_response(_json(response), api_version=version)
        logger.info(
            "signed in to site %s as %s using a %s",
            self._session.site_content_url or "(default)",
            self._session.user_id,
            credential.kind.value.replace("_", " "),
        )
        return self._session

    async def _sign_out(self) -> None:
        """Best effort. A session left open expires on its own, and failing a harvest at the
        very end because the sign-out call did not land would be absurd."""
        session = self._session
        self._session = None
        if session is None:
            return
        try:
            await self._client.post(
                f"/api/{session.api_version}/auth/signout", headers=session.headers
            )
        except Exception as exc:
            logger.debug("sign-out failed, letting the session expire: %s", exc)

    # ------------------------------------------------------------------- calling

    async def call(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        """One authenticated REST call, with the site's concurrency, backoff and re-sign-in."""

        async def send(session: Session | None, url: str) -> httpx.Response:
            assert session is not None  # authenticated=True below
            return await self._client.request(
                method,
                url,
                json=json_body,
                headers={**JSON_HEADERS, **session.headers},
                timeout=timeout or self._config.request_timeout,
            )

        return await self._authenticated(method, path, send)

    async def _authenticated(
        self,
        method: str,
        path: str,
        send: Callable[[Session | None, str], Awaitable[httpx.Response]],
    ) -> httpx.Response:
        """Session, concurrency slot, 429 backoff and 401 re-sign-in — in one place.

        All four here because they interact: a 401 raised while backing off must not trigger
        two sign-ins, and a re-sign-in must not spend a retry budget that belongs to
        throttling. Written as separate decorators they would each be simple and together
        they would be wrong.

        **Every call goes through this, downloads included.** An earlier version gave the
        download its own loop with the backoff but not the re-sign-in, and the download is
        where a long harvest spends its time — so a session expiring mid-run failed the one
        workbook most likely to be in flight when it happened. Two loops that were supposed
        to be the same, and were not.
        """
        signed_in_again = False
        while True:
            response = await self._with_backoff(method, path, send, authenticated=True)

            if response.status_code == 401 and not signed_in_again:
                # A session that has ended, not a credential that is wrong: the credential
                # worked minutes ago. Re-sign-in once — twice would mean the credential
                # really is being rejected, and looping on it is how a harvest hangs.
                logger.info("session expired mid-harvest; signing in again")
                self._session = None
                signed_in_again = True
                continue

            if response.status_code >= 400:
                raise self._failure(method, str(response.request.url.path), response)

            await self._throttle.succeeded()
            return response

    async def _with_backoff(
        self,
        method: str,
        path: str,
        send: Callable[[Session | None, str], Awaitable[httpx.Response]],
        *,
        authenticated: bool,
    ) -> httpx.Response:
        """A call, inside a concurrency slot, retried through 429s.

        The one place the site's cap and its backoff meet, so that every request the adapter
        makes — signed in or not, JSON or a download — obeys the same limits. Returns the
        response rather than raising on 4xx, because what a 401 means depends on whether the
        caller had a session to lose.
        """
        attempt = 0
        while True:
            session = await self.sign_in() if authenticated else None
            url = (
                path.format(version=session.api_version, site_id=session.site_id)
                if session is not None
                else path
            )

            async with self._throttle.slot():
                try:
                    response = await send(session, url)
                except httpx.HTTPError as exc:
                    # A reset connection, a DNS failure, a read timeout. `httpx` raises its
                    # own exception type, which is outside §6.1's taxonomy — and an error
                    # outside the taxonomy is treated by the platform as a *bug*, surfaced
                    # with a traceback, rather than as one workbook to retry. Classifying it
                    # here is the difference between a harvest that continues and a harvest
                    # that reports a platform fault (S2.1.2).
                    raise self._transport_failure(method, url, exc) from exc

            if response.status_code != 429:
                return response

            await self._throttle.attempt(
                attempt,
                retry_after_seconds(response.headers.get("Retry-After")),
                f"{method} {url}",
            )
            attempt += 1

    def _transport_failure(self, method: str, url: str, exc: httpx.HTTPError) -> AdapterError:
        """A network problem, named and **retryable**.

        Retryable because a reset connection means "not now", not "not ever": the workbook is
        fine and the next attempt very often succeeds. A workbook lost to a network blip is a
        workbook lost for no reason.
        """
        return AdapterError(
            f"{method} {url} could not reach Tableau: {type(exc).__name__}: {exc}",
            retryable=True,
        )

    def _failure(self, method: str, url: str, response: httpx.Response) -> AdapterError:
        detail = _error_detail(response)
        if response.status_code in (401, 403):
            credential = self._config.credential
            if credential is not None:
                return authentication_failure(credential, detail)
        # 5xx is the source having a bad moment; 4xx is this request being wrong, and
        # repeating it will produce the same answer. The distinction is what decides whether
        # the platform retries one workbook or records it and moves on (S2.1.2).
        return AdapterError(
            f"{method} {url} failed ({response.status_code}): {detail}",
            retryable=response.status_code >= 500 or response.status_code == 408,
        )

    # ------------------------------------------------------------------ the calls

    async def sites(self) -> list[dict[str, Any]]:
        """The sites this credential can see.

        A site-scoped credential sees one, which is the normal case and the shape §5.2
        assumes. A server administrator's sees all of them, and the adapter reports what it
        was given rather than assuming either.
        """
        session = await self.sign_in()
        try:
            response = await self.call("GET", "/api/{version}/sites")
        except AdapterError:
            # Not an error: a site-scoped credential is *forbidden* from listing sites, which
            # is the correct configuration for a client tenant. The session already knows the
            # one site it is on.
            return [
                {
                    "id": session.site_id,
                    "contentUrl": session.site_content_url,
                    "name": session.site_content_url or "Default",
                }
            ]
        return list((_json(response).get("sites") or {}).get("site") or [])

    async def workbooks(self) -> list[WorkbookRef]:
        """Every workbook on the site, paged.

        The REST listing is the *download* side of discovery: it carries the ids and the
        project the download call needs. The object graph — sheets, fields, upstream tables —
        comes from the Metadata API in one query rather than from 1,067 REST calls.
        """
        found: list[WorkbookRef] = []
        page = 1
        while True:
            response = await self.call(
                "GET",
                f"/api/{{version}}/sites/{{site_id}}/workbooks"
                f"?pageSize={self._config.page_size}&pageNumber={page}",
            )
            body = _json(response)
            items = (body.get("workbooks") or {}).get("workbook") or []
            found.extend(_workbook_ref(item) for item in items)

            pagination = body.get("pagination") or {}
            total = int(pagination.get("totalAvailable", len(found)) or 0)
            if len(found) >= total or not items:
                return found
            page += 1

    async def revisions(self, workbook_id: str) -> list[dict[str, Any]]:
        """A workbook's revision history, newest first.

        S2.2.1 requires the revision id on a fetch. Revision history can be disabled on a
        site, in which case Tableau answers 404 and the adapter falls back to the workbook's
        ``updatedAt`` — a weaker identity, and one the fetch says it is using rather than
        presenting as a revision it does not have.
        """
        try:
            response = await self.call(
                "GET", f"/api/{{version}}/sites/{{site_id}}/workbooks/{workbook_id}/revisions"
            )
        except AdapterError as exc:
            logger.debug("no revision history for %s: %s", workbook_id, exc)
            return []
        revisions = list((_json(response).get("revisions") or {}).get("revision") or [])
        revisions.sort(key=lambda item: int(item.get("revisionNumber", 0)), reverse=True)
        return revisions

    async def extract_refresh_schedules(self) -> dict[str, str]:
        """The site's extract-refresh tasks, as ``datasource or workbook name → schedule``.

        S2.2.2 asks for the refresh *schedule*, which the Metadata API does not have: it
        reports when an extract last refreshed. The schedule is a REST concept, and on Cloud
        it lives on the task itself while on Server it lives on a shared schedule the task
        points at — so both shapes are read.

        Missing is not a failure. Extract-refresh tasks require a site administrator on some
        deployments, and a harvest run by a content-reader credential is a legitimate and
        common configuration. An empty answer means "not visible to this credential", which
        the Datasource records as an absent schedule rather than as no schedule.
        """
        try:
            response = await self.call(
                "GET", "/api/{version}/sites/{site_id}/tasks/extractRefreshes"
            )
        except AdapterError as exc:
            logger.info("extract refresh schedules are not visible to this credential: %s", exc)
            return {}

        schedules: dict[str, str] = {}
        tasks = (_json(response).get("tasks") or {}).get("task") or []
        for task in tasks:
            refresh = task.get("extractRefresh") or {}
            schedule = refresh.get("schedule") or {}
            described = _describe_schedule(schedule) or str(refresh.get("type", ""))
            for holder in ("datasource", "workbook"):
                target = refresh.get(holder) or {}
                name = str(target.get("name", ""))
                if name and described:
                    schedules[name] = described
        return schedules

    async def download_workbook(self, workbook_id: str, *, include_extract: bool = False) -> bytes:
        """Download a workbook as ``.twb`` or ``.twbx``.

        ``includeExtract=false`` by default and that is a deliberate default, not a
        performance tweak: §16 forbids copying client data the platform does not need, and
        S2.2.2 states it outright — "extract data is not copied". The adapter needs the XML;
        an extract can be gigabytes of a client's actual data.
        """
        path = (
            "/api/{version}/sites/{site_id}/workbooks/" + workbook_id + "/content"
            f"?includeExtract={'true' if include_extract else 'false'}"
        )

        async def send(session: Session | None, url: str) -> httpx.Response:
            # Its own timeout and its own Accept header — a download is minutes and is not
            # JSON — but the same session, slot, backoff and re-sign-in as everything else.
            assert session is not None
            return await self._client.get(
                url,
                headers={**session.headers, "Accept": "*/*"},
                timeout=self._config.download_timeout,
            )

        response = await self._authenticated("GET", path, send)
        return response.content

    async def query_view_image(
        self, view_id: str, *, filters: str = "", resolution: str = "high"
    ) -> bytes:
        """§6.2 Screenshot: REST queryViewImage, for §10.6's advisory visual comparison.

        ``resolution=high`` asks Tableau for the higher-DPI render before this adapter
        resizes it — starting from more detail than the target size needs is cheap, and
        starting from less is a quality ceiling nothing downstream can lift.
        """
        query = f"resolution={resolution}"
        if filters:
            query += f"&{filters}"
        path = "/api/{version}/sites/{site_id}/views/" + view_id + f"/image?{query}"

        async def send(session: Session | None, url: str) -> httpx.Response:
            # Its own Accept header, like the download: an image is not JSON, and the
            # session, slot, backoff and re-sign-in are still shared with everything else.
            assert session is not None
            return await self._client.get(
                url,
                headers={**session.headers, "Accept": "image/png"},
                timeout=self._config.download_timeout,
            )

        response = await self._authenticated("GET", path, send)
        return response.content


# ------------------------------------------------------------------------ helpers


def _deployment_of(base_url: str, product_version: str) -> Deployment:
    host = base_url.lower()
    if "online.tableau.com" in host or ".tableau.com" in host:
        return Deployment.CLOUD
    # Cloud reports a version Server never uses; the host is the stronger signal, and this
    # is the fallback for a client using a vanity domain in front of Cloud.
    if product_version and not _SERVER_VERSION.match(product_version):
        return Deployment.CLOUD
    return Deployment.SERVER


def _describe_schedule(schedule: dict[str, Any]) -> str:
    """A schedule as a person would say it, not as Tableau models it.

    "Daily at 02:00" is what belongs on a Datasource node and in a migration conversation;
    ``{frequency: Daily, frequencyDetails: {start: 02:00:00}}`` is what Tableau returns. The
    raw form is not kept: it is Tableau's internal shape, and the target platform's scheduler
    has an entirely different one.
    """
    frequency = str(schedule.get("frequency", "")).strip()
    if not frequency:
        return str(schedule.get("name", ""))
    details = schedule.get("frequencyDetails") or {}
    start = str(details.get("start", "")).strip()
    intervals = details.get("intervals") or {}
    days = ", ".join(
        str(item.get("weekDay") or item.get("monthDay") or "")
        for item in (intervals.get("interval") or [])
        if item.get("weekDay") or item.get("monthDay")
    )
    parts = [frequency]
    if days:
        parts.append(f"on {days}")
    if start:
        parts.append(f"at {start[:5]}")
    return " ".join(parts)


def _workbook_ref(item: dict[str, Any]) -> WorkbookRef:
    project = item.get("project") or {}
    owner = item.get("owner") or {}
    return WorkbookRef(
        id=str(item.get("id", "")),
        name=str(item.get("name", "")),
        content_url=str(item.get("contentUrl", "")),
        project_id=str(project.get("id", "")),
        project_name=str(project.get("name", "")),
        owner_id=str(owner.get("id", "")),
        updated_at=str(item.get("updatedAt", "")),
        size_mb=int(item.get("size", 0) or 0),
        has_extracts=str(item.get("hasExtracts", "false")).lower() == "true",
    )


def _json(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError as exc:
        raise AdapterError(
            f"Tableau returned a body that is not JSON ({response.status_code}); the request "
            f"may have been answered by a proxy rather than by Tableau: {exc}",
            retryable=True,
        ) from exc
    return body if isinstance(body, dict) else {}


def _error_detail(response: httpx.Response) -> str:
    """Tableau's own error text, whichever shape it arrived in.

    The REST API answers JSON when asked and XML when something upstream answered instead —
    a proxy, a load balancer, a login page. Reporting "400 Bad Request" without the body
    turns a five-second diagnosis into an afternoon.
    """
    text = response.text or ""
    try:
        error = (response.json() or {}).get("error") or {}
        if error:
            return (
                f"{error.get('summary', '')}: {error.get('detail', '')} "
                f"(code {error.get('code', '?')})"
            ).strip()
    except ValueError:
        pass
    if text.lstrip().startswith("<"):
        try:
            root = ElementTree.fromstring(text)
            parts = [part.text or "" for part in root.iter() if part.text and part.text.strip()]
            if parts:
                return " ".join(parts)[:400]
        except ElementTree.ParseError:
            pass
    return text[:400] or response.reason_phrase


def rate_limited(reason: str, retry_after: float | None = None) -> RateLimited:
    return RateLimited(f"Tableau is rate limiting: {reason}", retry_after=retry_after)
