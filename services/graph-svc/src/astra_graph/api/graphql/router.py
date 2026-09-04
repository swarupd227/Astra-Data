"""Mounting GraphQL on the FastAPI app."""

from __future__ import annotations

from typing import Any

from fastapi import Request
from strawberry.fastapi import GraphQLRouter

from ...config import settings
from ...observability import QueryLog
from ..deps import get_principal, get_repository, get_role_set
from .context import GraphQLContext
from .schema import schema


async def get_context(request: Request) -> GraphQLContext:
    principal = get_principal(
        request.headers.get("X-Astra-Principal"), request.headers.get("X-Astra-Run-Id")
    )
    roles = get_role_set(request.headers.get("X-Astra-Roles"))
    return GraphQLContext(
        repository=get_repository(request),
        principal=principal,
        roles=roles,
        log=QueryLog(
            surface="graphql",
            principal=principal.value,
            roles=str(roles),
            run_id=principal.run_id,
        ),
    )


class LoggingGraphQLRouter(GraphQLRouter):  # type: ignore[type-arg]
    """GraphQL router that writes one query-log line per request (S1.1.2)."""

    async def process_result(self, request: Request, result: Any) -> Any:  # type: ignore[override]
        processed = await super().process_result(request, result)
        context = getattr(request.state, "astra_context", None)
        if isinstance(context, GraphQLContext):
            context.log.add(
                operations=[read["operation"] for read in context.reads],
                elements=sum(read["elements"] for read in context.reads),
                errors=len(result.errors or []),
            )
            context.log.finish(outcome="error" if result.errors else "ok")
        return processed


async def _context_getter(request: Request) -> GraphQLContext:
    context = await get_context(request)
    # Stashed so process_result can reach the same log entry after the query has run.
    request.state.astra_context = context
    return context


def build_router() -> GraphQLRouter:  # type: ignore[type-arg]
    # GraphiQL is a developer convenience, not part of the product surface; it is served
    # only where the deployment says it is local.
    return LoggingGraphQLRouter(
        schema,
        context_getter=_context_getter,
        graphql_ide="graphiql" if settings().env == "local" else None,
    )
