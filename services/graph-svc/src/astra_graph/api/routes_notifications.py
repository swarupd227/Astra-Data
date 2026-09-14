"""Per-user notification preferences -- story S10.5.2. See `notification_preferences.py`'s
own module docstring for the real recipient each of the four event types resolves to,
and for why "digest mode: daily" is a real, manually-triggered batch action rather than
a live cron job.

`GET`/`PUT /v1/notification-preferences` are deliberately open to any authenticated
principal, no specific role required -- the same "no role gate, this route carries
nothing another gated screen does not already render" posture `GET /v1/events`/`GET
/v1/explain/{metric_key}` already take (S10.1.2), read here as "everyone manages their
own preferences, nobody else's" rather than "nobody needs gating." A principal can only
ever read or write *its own* row -- there is no route that lists or edits another
principal's preferences.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from ..errors import InvalidRequestError
from ..notification_preferences import (
    CHANNELS,
    DIGEST_MODES,
    EVENT_TYPES,
    NotificationPreferenceError,
    NotificationPreferenceStore,
    send_pending_digests,
)
from .deps import ArtizentDep, PrincipalDep

router = APIRouter()


class SetPreferencesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channels: list[str] = Field(default_factory=list)
    events: list[str] = Field(default_factory=list)
    digest_mode: str = Field(min_length=1, max_length=16)


def _store(request: Request) -> NotificationPreferenceStore:
    store: NotificationPreferenceStore | None = getattr(request.app.state, "notification_preference_store", None)
    if store is None:  # pragma: no cover - set in every wiring path
        raise InvalidRequestError("notification preferences are not available on this deployment")
    return store


@router.get(
    "/v1/notification-preferences",
    tags=["notifications"],
    summary="The caller's own notification preferences (defaults, if never saved)",
)
async def get_notification_preferences(request: Request, principal: PrincipalDep) -> dict[str, Any]:
    preferences = await _store(request).get(principal.value)
    return preferences.as_dict()


@router.put(
    "/v1/notification-preferences",
    tags=["notifications"],
    summary="Save the caller's own channels, events and digest mode",
)
async def put_notification_preferences(
    body: SetPreferencesRequest, request: Request, principal: PrincipalDep,
) -> dict[str, Any]:
    try:
        preferences = await _store(request).set(
            principal.value, channels=body.channels, events=body.events, digest_mode=body.digest_mode,
        )
    except NotificationPreferenceError as exc:
        raise InvalidRequestError(str(exc)) from exc
    return preferences.as_dict()


@router.get(
    "/v1/notification-preferences:options",
    tags=["notifications"],
    summary="The real channel/event/digest-mode choices this deployment accepts",
)
async def get_notification_preference_options(
    request: Request, principal: PrincipalDep,
) -> dict[str, Any]:
    return {"channels": sorted(CHANNELS), "events": sorted(EVENT_TYPES), "digest_modes": sorted(DIGEST_MODES)}


@router.post(
    "/v1/notifications:send-digests",
    tags=["notifications"],
    summary="Batch and record every queued 'daily' notification, one digest per recipient/channel",
)
async def post_send_digests(request: Request, principal: PrincipalDep, roles: ArtizentDep) -> dict[str, Any]:
    sent = await send_pending_digests(_store(request))
    return {"digests_sent": [record.as_dict() for record in sent], "count": len(sent)}


__all__ = ["router"]
