"""Grammar issues, and the tracker they are mirrored into.

S1.4.3: "open grammar issue (creates a ticket with the construct text and locations)".

The issue is a platform record. §21 lists work tracking as *optional* — Azure DevOps or Jira,
one way, "for clients who require it" — and the mirror is R1.1 work, so a deployment with no
tracker at all must still be able to say which grammar gaps are open and what each is holding
up. ``IssueTracker`` is the seam the mirror plugs into; until then ``LocalIssueTracker``
records and does not pretend to file anything anywhere.

**A ticket is worth raising only if somebody can act on it.** So an issue carries the
construct verbatim, the places it was found, and the number of workbooks that would be
released by resolving it — the last is what decides which of thirty gaps to fix first, and
it is copied in rather than looked up later, because by the time somebody reads the issue
the estate will have moved.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Protocol

import asyncpg

from .ids import new_ulid

logger = logging.getLogger(__name__)

ISSUE_TABLE = "public.grammar_issue"

#: §15.2's rule, and the same floor the scope decisions use: a reason that is not a reason
#: is not a record. Ten characters is the shortest thing somebody will still understand.
MIN_DETAIL = 10

#: A construct's locations are copied onto the issue. More than this is a list nobody reads,
#: and the queue holds the live figure anyway.
MAX_LOCATIONS = 25


class IssueState(str, Enum):
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    RESOLVED = "RESOLVED"
    WONT_FIX = "WONT_FIX"

    @property
    def active(self) -> bool:
        return self in {IssueState.OPEN, IssueState.IN_PROGRESS}


class GrammarIssueError(Exception):
    """The issue as described cannot be raised."""


@dataclass(frozen=True, slots=True)
class GrammarIssue:
    """One construct the grammar cannot read, raised as work."""

    id: str
    construct: str
    summary: str
    detail: str
    state: IssueState
    opened_by: str
    adapter: str | None = None
    grammar_version: str | None = None
    locations: list[dict[str, Any]] = field(default_factory=list)
    occurrences: int = 0
    workbooks_held: int = 0
    external_ref: str | None = None
    external_url: str | None = None
    opened_at: str | None = None
    resolved_by: str | None = None
    resolved_at: str | None = None
    resolution: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "construct": self.construct,
            "summary": self.summary,
            "detail": self.detail,
            "state": self.state.value,
            "active": self.state.active,
            "adapter": self.adapter,
            "grammar_version": self.grammar_version,
            # A snapshot, deliberately: the estate moves, and an issue should describe the
            # evidence it was raised on rather than wherever the construct is today.
            "locations": self.locations,
            "occurrences_when_raised": self.occurrences,
            "workbooks_held_when_raised": self.workbooks_held,
            "external": {"ref": self.external_ref, "url": self.external_url},
            "opened_by": self.opened_by,
            "opened_at": self.opened_at,
            "resolved_by": self.resolved_by,
            "resolved_at": self.resolved_at,
            "resolution": self.resolution,
        }


class IssueStore(Protocol):
    async def open(self, issue: GrammarIssue) -> GrammarIssue: ...

    async def get(self, issue_id: str) -> GrammarIssue | None: ...

    async def by_construct(self) -> dict[str, GrammarIssue]: ...

    async def recent(self, *, limit: int = 100) -> list[GrammarIssue]: ...

    async def resolve(
        self, issue_id: str, *, state: IssueState, resolution: str, resolved_by: str
    ) -> GrammarIssue | None: ...


class IssueTracker(Protocol):
    """Where an issue is mirrored, for clients who require one (§21, R1.1)."""

    @property
    def kind(self) -> str: ...

    async def mirror(self, issue: GrammarIssue) -> tuple[str | None, str | None]:
        """Push the issue outward. Returns ``(external_ref, external_url)``."""
        ...


class LocalIssueTracker:
    """No tracker. The issue lives here and nowhere else.

    Correct until a client asks for the mirror: §21 makes work tracking optional, and a
    platform that silently dropped issues on the floor because no tracker was configured
    would be worse than one that says it is holding them itself.
    """

    kind = "local"

    async def mirror(self, issue: GrammarIssue) -> tuple[str | None, str | None]:
        logger.info(
            "grammar issue %s recorded locally; no work tracker is configured", issue.id
        )
        return None, None


def new_issue(
    *,
    construct: str,
    summary: str,
    detail: str,
    opened_by: str,
    adapter: str | None = None,
    grammar_version: str | None = None,
    locations: list[dict[str, Any]] | None = None,
    occurrences: int = 0,
    workbooks_held: int = 0,
) -> GrammarIssue:
    detail = detail.strip()
    if len(detail) < MIN_DETAIL:
        raise GrammarIssueError(
            f"a grammar issue needs at least {MIN_DETAIL} characters of detail; whoever "
            f"picks it up will not have been in the conversation"
        )
    if not construct.strip():
        raise GrammarIssueError("a grammar issue is about a construct; none was given")
    return GrammarIssue(
        id=f"gi_{new_ulid()}",
        construct=construct,
        summary=summary.strip() or f"Grammar cannot read {construct}",
        detail=detail,
        state=IssueState.OPEN,
        opened_by=opened_by,
        adapter=adapter,
        grammar_version=grammar_version,
        locations=(locations or [])[:MAX_LOCATIONS],
        occurrences=occurrences,
        workbooks_held=workbooks_held,
        opened_at=_now(),
    )


class PostgresIssueStore:
    def __init__(self, pool: asyncpg.Pool, *, graph_name: str) -> None:
        self._pool = pool
        self._graph = graph_name

    async def open(self, issue: GrammarIssue) -> GrammarIssue:
        async with self._pool.acquire() as conn:
            try:
                row = await conn.fetchrow(
                    f"""
                    INSERT INTO {ISSUE_TABLE}
                        (id, graph, construct, adapter, grammar_version, state, summary,
                         detail, locations, occurrences, workbooks_held, external_ref,
                         external_url, opened_by)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10, $11, $12, $13, $14)
                 RETURNING *
                    """,
                    issue.id,
                    self._graph,
                    issue.construct,
                    issue.adapter,
                    issue.grammar_version,
                    issue.state.value,
                    issue.summary,
                    issue.detail,
                    json.dumps(issue.locations),
                    issue.occurrences,
                    issue.workbooks_held,
                    issue.external_ref,
                    issue.external_url,
                    issue.opened_by,
                )
            except Exception as exc:
                if "grammar_issue_one_open_idx" in str(exc):
                    raise GrammarIssueError(
                        f"an issue is already open for {issue.construct!r}. A second is not "
                        f"a second problem — add to the open one."
                    ) from exc
                raise
        return _from_row(row)

    async def get(self, issue_id: str) -> GrammarIssue | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT * FROM {ISSUE_TABLE} WHERE graph = $1 AND id = $2",
                self._graph,
                issue_id,
            )
        return _from_row(row) if row else None

    async def by_construct(self) -> dict[str, GrammarIssue]:
        """The active issue for each construct, in one read.

        The queue renders one row per construct and each needs to know whether it is
        already raised; asking per row would be a query per construct on every screen.
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT * FROM {ISSUE_TABLE} WHERE graph = $1 "
                f"AND state IN ('OPEN', 'IN_PROGRESS')",
                self._graph,
            )
        return {row["construct"]: _from_row(row) for row in rows}

    async def recent(self, *, limit: int = 100) -> list[GrammarIssue]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT * FROM {ISSUE_TABLE} WHERE graph = $1 "
                f"ORDER BY opened_at DESC LIMIT $2",
                self._graph,
                limit,
            )
        return [_from_row(row) for row in rows]

    async def resolve(
        self, issue_id: str, *, state: IssueState, resolution: str, resolved_by: str
    ) -> GrammarIssue | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                UPDATE {ISSUE_TABLE}
                   SET state = $3, resolution = $4, resolved_by = $5, resolved_at = now()
                 WHERE graph = $1 AND id = $2 AND state IN ('OPEN', 'IN_PROGRESS')
             RETURNING *
                """,
                self._graph,
                issue_id,
                state.value,
                resolution,
                resolved_by,
            )
        return _from_row(row) if row else None


class InMemoryIssueStore:
    def __init__(self) -> None:
        self.issues: dict[str, GrammarIssue] = {}

    async def open(self, issue: GrammarIssue) -> GrammarIssue:
        if any(
            existing.construct == issue.construct and existing.state.active
            for existing in self.issues.values()
        ):
            raise GrammarIssueError(
                f"an issue is already open for {issue.construct!r}. A second is not a "
                f"second problem — add to the open one."
            )
        self.issues[issue.id] = issue
        return issue

    async def get(self, issue_id: str) -> GrammarIssue | None:
        return self.issues.get(issue_id)

    async def by_construct(self) -> dict[str, GrammarIssue]:
        return {
            issue.construct: issue for issue in self.issues.values() if issue.state.active
        }

    async def recent(self, *, limit: int = 100) -> list[GrammarIssue]:
        return list(self.issues.values())[:limit]

    async def resolve(
        self, issue_id: str, *, state: IssueState, resolution: str, resolved_by: str
    ) -> GrammarIssue | None:
        from dataclasses import replace

        issue = self.issues.get(issue_id)
        if issue is None or not issue.state.active:
            return None
        resolved = replace(
            issue,
            state=state,
            resolution=resolution,
            resolved_by=resolved_by,
            resolved_at=_now(),
        )
        self.issues[issue_id] = resolved
        return resolved


def _from_row(row: asyncpg.Record) -> GrammarIssue:
    locations = row["locations"]
    return GrammarIssue(
        id=row["id"],
        construct=row["construct"],
        summary=row["summary"],
        detail=row["detail"],
        state=IssueState(row["state"]),
        opened_by=row["opened_by"],
        adapter=row["adapter"],
        grammar_version=row["grammar_version"],
        locations=json.loads(locations) if isinstance(locations, str) else list(locations),
        occurrences=row["occurrences"],
        workbooks_held=row["workbooks_held"],
        external_ref=row["external_ref"],
        external_url=row["external_url"],
        opened_at=_iso(row["opened_at"]),
        resolved_by=row["resolved_by"],
        resolved_at=_iso(row["resolved_at"]),
        resolution=row["resolution"],
    )


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    rendered: str = value.astimezone(UTC).isoformat(timespec="milliseconds")
    return rendered.replace("+00:00", "Z")


__all__ = [
    "ISSUE_TABLE",
    "MAX_LOCATIONS",
    "MIN_DETAIL",
    "GrammarIssue",
    "GrammarIssueError",
    "InMemoryIssueStore",
    "IssueState",
    "IssueStore",
    "IssueTracker",
    "LocalIssueTracker",
    "PostgresIssueStore",
    "new_issue",
]
