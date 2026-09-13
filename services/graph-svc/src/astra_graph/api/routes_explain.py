"""``GET /v1/explain/{metric_key}`` — story S10.1.2's own "every number on a screen has
an 'explain' affordance that opens the query or the events behind it." See ``explain.
py``'s own module docstring for what the registry holds and why coverage is a
representative first pass, not literally every digit on every screen.

**Deliberately open, the same reasoning `GET /v1/events`/`GET /v1/events:stream` already
give.** A query's own text is not client data — it is the platform's own code, already
implied by whatever number the console already showed this exact caller. Gating it would
add a permission check that protects nothing a role gate on the *number itself* does not
already protect.
"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter

from ..errors import ElementNotFoundError
from ..explain import EXPLAIN_REGISTRY

router = APIRouter()


@router.get(
    "/v1/explain/{metric_key}",
    tags=["events"],
    summary="The real query or computation behind one console figure",
)
async def explain(metric_key: str) -> dict[str, object]:
    entry = EXPLAIN_REGISTRY.get(metric_key)
    if entry is None:
        raise ElementNotFoundError(
            f"no explain entry for '{metric_key}' — it may not be wired up yet "
            f"(explain.py's own module docstring names this a representative first pass)"
        )
    return asdict(entry)
