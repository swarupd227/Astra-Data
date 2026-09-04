"""The read-only Cypher endpoint.

S1.1.2: "A read-only Cypher endpoint is available to Artizent roles with a 30-second
timeout and a 10,000-row cap."

It exists so a migration engineer can answer a lineage question the typed API does not
have a field for, without waiting for a release — "I can answer any lineage question
without a new feature", in the story's words. Everything that makes that safe is
elsewhere: ``cypher.py`` rejects what it can read as unsafe, and the repository runs what
survives inside a PostgreSQL READ ONLY transaction.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from ..cypher import CypherRejected, accept
from ..errors import InvalidRequestError
from .deps import ArtizentDep, PrincipalDep, RepositoryDep, open_query_log

#: S1.1.2, both figures.
TIMEOUT_SECONDS = 30
ROW_LIMIT = 10_000

router = APIRouter()


class CypherRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(
        min_length=1,
        description="A single read-only Cypher statement ending in a named RETURN clause.",
    )
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="Parameters, referenced in the query as $name.",
    )
    columns: list[str] | None = Field(
        default=None,
        description="Result column names. Derived from the RETURN clause when omitted; "
        "supply them for a RETURN the deriver cannot read.",
    )


class CypherResponse(BaseModel):
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int
    truncated: bool = Field(
        description=f"True when the result was cut at {ROW_LIMIT} rows. Narrow the query; "
        f"the rows returned are not a sample, they are the first {ROW_LIMIT}."
    )
    duration_ms: float
    timeout_seconds: int = TIMEOUT_SECONDS
    row_limit: int = ROW_LIMIT


@router.post(
    "/v1/cypher",
    response_model=CypherResponse,
    tags=["query"],
    summary="Run a read-only Cypher query against the Estate Graph",
)
async def run_cypher(
    body: CypherRequest,
    repository: RepositoryDep,
    principal: PrincipalDep,
    roles: ArtizentDep,
) -> CypherResponse:
    log = open_query_log("cypher", principal, roles)
    try:
        accepted = accept(body.query, columns=body.columns)
    except CypherRejected as exc:
        log.add(rejected=exc.code, query=body.query)
        log.finish(outcome="rejected")
        raise InvalidRequestError(str(exc)) from exc

    log.add(query=accepted.text, columns=list(accepted.columns))
    try:
        rows, truncated = await repository.run_read_only_cypher(
            accepted.text,
            accepted.columns,
            body.params,
            timeout_seconds=TIMEOUT_SECONDS,
            row_limit=ROW_LIMIT,
        )
    except Exception as exc:
        log.add(rows=0)
        log.finish(outcome=type(exc).__name__)
        raise

    log.add(rows=len(rows), truncated=truncated)
    log.finish()
    return CypherResponse(
        columns=list(accepted.columns),
        rows=rows,
        row_count=len(rows),
        truncated=truncated,
        duration_ms=round(log.duration_ms, 2),
    )


__all__ = ["CypherRequest", "CypherResponse", "router"]
