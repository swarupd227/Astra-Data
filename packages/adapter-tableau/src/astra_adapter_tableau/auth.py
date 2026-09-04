"""Signing in to Tableau — personal access token and connected app.

    "…with personal-access-token and connected-app authentication" — S2.2.1
    "Personal access token or Connected App (JWT) held in Key Vault; site-scoped" — §6.2

Both end in the same place: `POST /api/{version}/auth/signin` returns a credentials token, a
site id and a user id, and every later call carries the token in `X-Tableau-Auth`. What
differs is what is *sent*.

**A personal access token** is a name and a secret belonging to a person. Tableau invalidates
the token's previous session when it is used again, so two workers sharing one PAT will
repeatedly sign each other out — which looks exactly like an intermittent authentication
failure and is the reason a deployment should reach for a connected app instead.

**A connected app** is an application identity. The adapter mints a short-lived JWT signed
with the app's secret and sends *that*; Tableau validates it against the registered app. The
JWT names the user it acts as, so a harvest is still attributable to somebody — there is no
unattributed connected-app session.

**Sessions expire, and the adapter re-signs in.** Tableau's default is 240 minutes and a
harvest of a large estate outlives it. A 401 mid-harvest is not a failure; it is a session
that has ended, and treating it as a failure would lose the rest of the run.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import jwt
from astra_adapter import AdapterError

from .config import AuthKind, Credential

logger = logging.getLogger(__name__)

#: How long a minted connected-app JWT is valid. Tableau caps it at ten minutes and rejects
#: anything longer; five leaves room for clock skew on both sides without being so short that
#: a slow sign-in expires the token it is presenting.
JWT_LIFETIME_SECONDS = 300

#: The scopes a connected-app JWT must claim for what this adapter does. Tableau refuses a
#: token whose scopes do not cover the calls made with it, and it refuses at the point of use
#: rather than at sign-in — so a missing scope shows up as an unrelated call failing.
JWT_SCOPES = (
    "tableau:content:read",
    "tableau:workbooks:read",
    "tableau:views:read",
    "tableau:projects:read",
    "tableau:users:read",
)


@dataclass(slots=True)
class Session:
    """An authenticated Tableau session."""

    token: str = field(repr=False)
    site_id: str
    site_content_url: str
    user_id: str
    api_version: str
    expires_at: float

    @property
    def headers(self) -> dict[str, str]:
        return {"X-Tableau-Auth": self.token}

    def is_fresh(self, *, margin: float = 60.0) -> bool:
        """Fresh with a margin, so a call is not started with a token about to expire.

        Without the margin a long download begun a second before expiry fails halfway
        through, having already spent the transfer — the most expensive way to discover a
        session has ended.
        """
        return time.time() < self.expires_at - margin

    def __str__(self) -> str:  # pragma: no cover - defensive
        return f"<tableau session site={self.site_content_url or '(default)'}>"


def sign_in_payload(credential: Credential, site: str) -> dict[str, Any]:
    """The body of `POST /api/{version}/auth/signin`, for either credential kind."""
    credential.validate()

    if credential.kind is AuthKind.PERSONAL_ACCESS_TOKEN:
        return {
            "credentials": {
                "personalAccessTokenName": credential.token_name,
                "personalAccessTokenSecret": credential.secret,
                "site": {"contentUrl": site},
            }
        }

    return {
        "credentials": {
            "jwt": mint_connected_app_token(credential),
            "site": {"contentUrl": site},
        }
    }


def mint_connected_app_token(credential: Credential, *, now: float | None = None) -> str:
    """A short-lived JWT for a connected app.

    HS256 with the app's secret, which is what Tableau's connected apps use. The ``kid``
    header carries the *secret id* rather than the client id — Tableau uses it to select
    which of an app's secrets to validate against, and getting these two the wrong way round
    produces an authentication failure that names neither.
    """
    issued = now if now is not None else time.time()
    claims = {
        "iss": credential.client_id,
        "exp": int(issued + JWT_LIFETIME_SECONDS),
        "jti": str(uuid.uuid4()),
        "aud": "tableau",
        "sub": credential.username,
        "scp": list(JWT_SCOPES),
    }
    try:
        token = jwt.encode(
            claims,
            credential.secret,
            algorithm="HS256",
            headers={"kid": credential.secret_id, "iss": credential.client_id},
        )
        # PyJWT 1.x returned bytes and 2.x returns str. Normalised rather than pinned,
        # because an adapter image is built from a lock file this package does not own.
        return token.decode() if isinstance(token, bytes) else token
    except Exception as exc:  # pragma: no cover - a malformed secret
        raise AdapterError(
            f"could not mint a connected-app token for {credential.client_id!r}: {exc}",
            retryable=False,
        ) from exc


def session_from_response(
    body: dict[str, Any], *, api_version: str, lifetime_seconds: float = 240 * 60
) -> Session:
    """Read a sign-in response.

    Tableau does not return a session lifetime, so the deployment's configured one is assumed
    (240 minutes by default) and treated as a hint: the adapter re-signs in on a 401 whatever
    this says. The expiry is what stops it presenting a token it already knows is stale, not
    what it relies on for correctness.
    """
    credentials = (body or {}).get("credentials") or {}
    token = credentials.get("token")
    site = credentials.get("site") or {}
    user = credentials.get("user") or {}

    if not token:
        raise AdapterError(
            "Tableau accepted the sign-in request but returned no credentials token; the "
            "response was not the shape the REST API documents",
            retryable=False,
        )

    return Session(
        token=str(token),
        site_id=str(site.get("id", "")),
        site_content_url=str(site.get("contentUrl", "")),
        user_id=str(user.get("id", "")),
        api_version=api_version,
        expires_at=time.time() + lifetime_seconds,
    )


def authentication_failure(credential: Credential, detail: str) -> AdapterError:
    """A rejected credential, named as such and **not** retryable.

    The taxonomy matters here more than anywhere (S2.1.2): retrying with the same rejected
    credential fails identically, and a retryable classification would mark every workbook in
    a 1,067-workbook estate as individually failed rather than stopping the run once.
    """
    return AdapterError(
        f"Tableau rejected the {credential.describe()}: {detail}. Retrying with the same "
        f"credential will fail the same way — check the secret, the site, and (for a "
        f"connected app) that the client id, secret id and username all belong together.",
        retryable=False,
    )
