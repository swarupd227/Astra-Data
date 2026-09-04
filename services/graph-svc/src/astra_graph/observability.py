"""Query logging.

S1.1.2: "Every query is logged with principal and duration."

Every read of the graph produces exactly one line, whatever surface it arrived on, with
the principal that asked, how long it took and how much it returned. What is deliberately
absent is the data: a query result can contain a field name or a custom SQL literal the
client classifies as restricted (spec §18.3), so the log records the shape of the answer
and never the answer.

The raw Cypher endpoint is the exception, and records the query text: it is the one
surface where a caller composes arbitrary traversal, and an auditor asking what someone
ran against the estate needs to be able to see it. The text is a query over metadata,
never over row-level data — the Proof Engine holds that, not the graph.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from types import TracebackType
from typing import Any

logger = logging.getLogger("astra_graph.query")


@dataclass
class QueryLog:
    """One query, from arrival to response."""

    surface: str
    """graphql, cypher or rest."""

    principal: str
    roles: str = "-"
    run_id: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)
    _started: float = field(default_factory=time.perf_counter, init=False)
    _duration_ms: float | None = field(default=None, init=False)

    @property
    def duration_ms(self) -> float:
        if self._duration_ms is not None:
            return self._duration_ms
        return (time.perf_counter() - self._started) * 1000

    def add(self, **detail: Any) -> None:
        self.detail.update(detail)

    def finish(self, *, outcome: str = "ok") -> None:
        self._duration_ms = (time.perf_counter() - self._started) * 1000
        context: dict[str, Any] = {
            "surface": self.surface,
            "principal": self.principal,
            "roles": self.roles,
            "duration_ms": round(self._duration_ms, 2),
            "outcome": outcome,
        }
        if self.run_id:
            context["run_id"] = self.run_id
        context.update(self.detail)
        logger.info("graph query", extra={"context": context})

    def __enter__(self) -> QueryLog:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.finish(outcome="ok" if exc is None else type(exc).__name__)
