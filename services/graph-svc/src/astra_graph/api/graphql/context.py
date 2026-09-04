"""Per-request context for GraphQL resolvers.

Carries the repository, the caller, and the query log entry. S1.1.2: "Every query is
logged with principal and duration" — the entry is opened when the request arrives and
written when it finishes, so the duration covers the whole operation rather than one
resolver.
"""

from __future__ import annotations

from typing import Any

from strawberry.fastapi import BaseContext

from ...graph import GraphRepository
from ...observability import QueryLog
from ...principal import Principal
from ...roles import RoleSet


class GraphQLContext(BaseContext):
    """Strawberry requires a custom context to derive from BaseContext."""

    def __init__(
        self,
        *,
        repository: GraphRepository,
        principal: Principal,
        roles: RoleSet,
        log: QueryLog,
    ) -> None:
        super().__init__()
        self.repository = repository
        self.principal = principal
        self.roles = roles
        self.log = log
        self.reads: list[dict[str, Any]] = []

    def record_read(
        self, operation: str, *, elements: int, detail: dict[str, Any] | None = None
    ) -> None:
        """Note one resolver's work, so the request's log line says what it did."""
        entry: dict[str, Any] = {"operation": operation, "elements": elements}
        if detail:
            entry.update(detail)
        self.reads.append(entry)
