"""How a Tableau adapter worker is configured — story S2.2.1.

**The adapter holds its own credential, and it never crosses the RPC.** The platform names a
credential (`tableau/rqa`) and resolves it for its own records; the *adapter worker* is a
separate pod (§5.2, §5.4) with its own Key Vault access, scoped to a site — §5.2's scale unit
for `adapter-tableau` is literally "per site parallelism". So the secret reaches the adapter
through its own environment and never travels the adapter RPC, which extends the platform's
existing rule ("a credential is never sent over the API, only a reference") to the one channel
S2.1.1 added. ADR 0015 records this, and it is an open question there: whether the reference
should travel so one worker can serve several sites.

**Server and Cloud are one adapter.** They differ in three ways that matter here — how a site
is addressed, which REST API version is available, and how fast they throttle — and all three
are read from the deployment rather than configured, so an operator does not have to know
which they have.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from enum import Enum

from astra_adapter import AdapterError

#: §6.2: "Adaptive concurrency per site". The story fixes the default.
DEFAULT_CONCURRENCY = 4

#: The REST API version to negotiate *down* from. 3.14 is Tableau Server 2021.4, which the
#: story names as the floor — asking for it first means a 2021.4 server answers and anything
#: newer answers too, and the real version comes back in the response rather than being
#: guessed from a number.
MINIMUM_API_VERSION = "3.14"

#: Tableau Server releases and their REST API versions, for reporting a floor violation in
#: terms an operator recognises. Not exhaustive and not used for feature detection — the
#: server tells us its product version, and this only translates it.
SERVER_FLOOR = "2021.4"

ENV_PREFIX = "ASTRA_TABLEAU"


class AuthKind(str, Enum):
    """The two the story names.

    Both are site-scoped and neither is a password. A personal access token belongs to a
    person and expires; a connected app is an application identity that mints short-lived
    JWTs and is what a platform deployment should use in the end (§6.2: "Personal access
    token or Connected App (JWT) held in Key Vault; site-scoped").
    """

    PERSONAL_ACCESS_TOKEN = "personal_access_token"
    CONNECTED_APP = "connected_app"


@dataclass(frozen=True, slots=True)
class Credential:
    """What the adapter needs to sign in, and nothing more.

    ``secret`` is excluded from ``repr`` for the same reason ``SourceCredential`` excludes it
    on the platform side: the commonest way a secret escapes is a log line nobody wrote on
    purpose.
    """

    kind: AuthKind
    secret: str = field(repr=False)

    token_name: str = ""
    """PAT only: the token's name, which Tableau requires alongside its value."""

    client_id: str = ""
    secret_id: str = ""
    username: str = ""
    """Connected app only. Tableau's JWT is minted *for a user*: the app authenticates, and
    the ``sub`` claim says whom it is acting as. There is no such thing as an unattributed
    connected-app session, which is a property worth having — every harvest is attributable."""

    def describe(self) -> str:
        if self.kind is AuthKind.PERSONAL_ACCESS_TOKEN:
            return f"personal access token {self.token_name!r}"
        return f"connected app {self.client_id!r} acting as {self.username!r}"

    def __str__(self) -> str:  # pragma: no cover - defensive
        return f"<tableau credential: {self.describe()}>"

    @classmethod
    def from_json(cls, raw: str) -> Credential:
        """Parse a credential document, as a Key Vault secret holds it.

        JSON rather than a bare token because a connected app needs four fields and a PAT
        needs two, and a format that could only carry one value would have forced the two
        auth kinds into two configuration mechanisms.
        """
        try:
            body = json.loads(raw)
        except ValueError as exc:
            raise AdapterError(
                f"the Tableau credential is not JSON. It should look like "
                f'{{"kind": "personal_access_token", "token_name": "...", "secret": "..."}} '
                f'or {{"kind": "connected_app", "client_id": "...", '
                f'"secret_id": "...", "secret": "...", "username": "..."}}: {exc}',
                retryable=False,
            ) from exc
        if not isinstance(body, dict):
            raise AdapterError("the Tableau credential must be a JSON object", retryable=False)

        try:
            kind = AuthKind(str(body.get("kind", "")))
        except ValueError as exc:
            raise AdapterError(
                f"unknown Tableau credential kind {body.get('kind')!r}; expected one of "
                f"{', '.join(k.value for k in AuthKind)}",
                retryable=False,
            ) from exc

        secret = str(body.get("secret") or "")
        if not secret:
            raise AdapterError("the Tableau credential has no secret", retryable=False)

        credential = cls(
            kind=kind,
            secret=secret,
            token_name=str(body.get("token_name") or ""),
            client_id=str(body.get("client_id") or ""),
            secret_id=str(body.get("secret_id") or ""),
            username=str(body.get("username") or ""),
        )
        credential.validate()
        return credential

    def validate(self) -> None:
        """Refuse an incomplete credential here rather than at the first 401.

        A missing ``username`` on a connected app produces a JWT Tableau rejects with a
        generic authentication error, and an operator then has to work out which of four
        fields was wrong from a message that names none of them.
        """
        if self.kind is AuthKind.PERSONAL_ACCESS_TOKEN and not self.token_name:
            raise AdapterError(
                "a personal access token needs its 'token_name' as well as its secret; "
                "Tableau identifies the token by name",
                retryable=False,
            )
        if self.kind is AuthKind.CONNECTED_APP:
            missing = [
                name for name in ("client_id", "secret_id", "username") if not getattr(self, name)
            ]
            if missing:
                raise AdapterError(
                    f"a connected app credential needs {', '.join(missing)}. Tableau mints "
                    f"its JWT for a named user, so there is no unattributed connected-app "
                    f"session.",
                    retryable=False,
                )


@dataclass(frozen=True, slots=True)
class TableauConfig:
    """Everything a Tableau adapter worker needs.

    ``site`` is Tableau's *content URL*, not its display name — the empty string is the
    Default site on Server, and on Cloud it is always a real value. Getting this wrong is the
    single commonest Tableau integration mistake, so the field is named after what it is.
    """

    base_url: str
    site: str = ""
    credential: Credential | None = None

    concurrency: int = DEFAULT_CONCURRENCY
    """§6.2's "adaptive concurrency per site", and S2.2.1's configurable cap."""

    page_size: int = 200
    """Metadata API page size. Tableau's GraphQL endpoint degrades badly on large pages and
    times out rather than paging, so this is deliberately below what it will accept."""

    request_timeout: float = 120.0
    download_timeout: float = 900.0
    """Separate, because a workbook download is minutes and a metadata query is seconds. One
    timeout for both is either too short for the download or useless for the query."""

    max_retries: int = 5
    verify_tls: bool = True
    """Off only for a client's self-signed staging server, and never silently: the adapter
    logs it at start-up so a deployment that turned it off says so."""

    @property
    def site_label(self) -> str:
        return self.site or "(default)"

    def with_credential(self, credential: Credential) -> TableauConfig:
        return TableauConfig(
            base_url=self.base_url,
            site=self.site,
            credential=credential,
            concurrency=self.concurrency,
            page_size=self.page_size,
            request_timeout=self.request_timeout,
            download_timeout=self.download_timeout,
            max_retries=self.max_retries,
            verify_tls=self.verify_tls,
        )

    @classmethod
    def from_environment(cls, environ: dict[str, str] | None = None) -> TableauConfig:
        """Read the worker's configuration.

        ``ASTRA_TABLEAU_URL``, ``ASTRA_TABLEAU_SITE``, ``ASTRA_TABLEAU_CREDENTIAL`` (the JSON
        document), ``ASTRA_TABLEAU_CONCURRENCY``, ``ASTRA_TABLEAU_VERIFY_TLS``.
        """
        env = environ if environ is not None else dict(os.environ)
        base_url = env.get(f"{ENV_PREFIX}_URL", "").strip()
        if not base_url:
            raise AdapterError(
                f"{ENV_PREFIX}_URL is not set. A Tableau adapter worker is configured for one "
                f"deployment: set it to the Server or Cloud base URL, for example "
                f"https://tableau.client.example or https://10ax.online.tableau.com",
                retryable=False,
            )

        raw_credential = env.get(f"{ENV_PREFIX}_CREDENTIAL", "").strip()
        credential = Credential.from_json(raw_credential) if raw_credential else None

        return cls(
            base_url=base_url.rstrip("/"),
            site=env.get(f"{ENV_PREFIX}_SITE", ""),
            credential=credential,
            concurrency=_positive_int(env, f"{ENV_PREFIX}_CONCURRENCY", DEFAULT_CONCURRENCY),
            page_size=_positive_int(env, f"{ENV_PREFIX}_PAGE_SIZE", 200),
            max_retries=_positive_int(env, f"{ENV_PREFIX}_MAX_RETRIES", 5),
            verify_tls=env.get(f"{ENV_PREFIX}_VERIFY_TLS", "1").lower() not in {"0", "false", "no"},
        )


def _positive_int(env: dict[str, str], name: str, default: int) -> int:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise AdapterError(f"{name} must be a whole number, not {raw!r}", retryable=False) from exc
    if value < 1:
        raise AdapterError(
            f"{name} must be at least 1; {value} would mean the adapter never calls the source",
            retryable=False,
        )
    return value
