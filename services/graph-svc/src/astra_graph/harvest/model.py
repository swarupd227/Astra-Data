"""What a harvest run is, and how its progress is reported.

S1.2.1: "progress is visible per project with counts of workbooks queued, parsed, failed",
and "failures do not stop the run and are listed with the error".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class HarvestMode(str, Enum):
    """How much of the scope a run reconsiders (S1.2.4)."""

    FULL = "FULL"
    """Fetch every workbook and compare content. What an operator asks for by hand, and
    the only honest mode against a source that cannot report when a workbook changed."""

    INCREMENTAL = "INCREMENTAL"
    """Trust the source's own ``updatedAt`` from the enumeration and fetch only what moved.
    What a schedule uses: over a long programme the alternative is re-downloading a
    thousand unchanged workbooks every night."""


class HarvestState(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    """Finished. Some workbooks may have failed; ``failed`` says how many."""

    FAILED = "FAILED"
    """The run itself could not proceed — bad scope, no credential, source unreachable."""

    CANCELLED = "CANCELLED"

    @property
    def terminal(self) -> bool:
        return self in {HarvestState.COMPLETED, HarvestState.FAILED, HarvestState.CANCELLED}


class WorkbookOutcome(str, Enum):
    PARSED = "parsed"
    SKIPPED_UNCHANGED = "skipped_unchanged"
    """Spec §8.4: a re-run on an unchanged workbook is a no-op. S1.2.1 names this."""

    SKIPPED_NOT_MODIFIED = "skipped_not_modified"
    """S1.2.4: the source said it had not changed, so it was never fetched. Distinct from
    ``skipped_unchanged``, which was fetched and found identical — the difference is the
    download, and it is the whole saving an incremental run exists for."""

    FAILED = "failed"
    HELD_PARSE_QUALITY = "held_parse_quality"
    """Parsed, but below the threshold: written, and held out of CLUSTERED until a
    Platform Engineer reviews it (spec §4.1.4). S1.2.2 builds the queue; this records it."""


@dataclass(slots=True)
class ProjectProgress:
    """Counts for one project. The unit the Estate Explorer reports against."""

    project: str
    queued: int = 0
    parsed: int = 0
    skipped_unchanged: int = 0
    skipped_not_modified: int = 0
    held: int = 0
    failed: int = 0

    @property
    def done(self) -> int:
        return (
            self.parsed
            + self.skipped_unchanged
            + self.skipped_not_modified
            + self.held
            + self.failed
        )

    @property
    def remaining(self) -> int:
        return max(0, self.queued - self.done)

    def as_dict(self) -> dict[str, Any]:
        return {
            "project": self.project,
            "queued": self.queued,
            "parsed": self.parsed,
            "skipped_unchanged": self.skipped_unchanged,
            "skipped_not_modified": self.skipped_not_modified,
            "held": self.held,
            "failed": self.failed,
            "remaining": self.remaining,
        }


@dataclass(slots=True)
class HarvestFailure:
    """One workbook that could not be harvested, and why.

    Carries enough to act on without opening a log: which workbook, in which project, at
    which stage, and the error as raised.
    """

    workbook_luid: str
    workbook_name: str
    project: str
    stage: str
    """enumerate, fetch, parse or write."""

    error: str
    retryable: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "workbook_luid": self.workbook_luid,
            "workbook_name": self.workbook_name,
            "project": self.project,
            "stage": self.stage,
            "error": self.error,
            "retryable": self.retryable,
        }


@dataclass(slots=True)
class HarvestProgress:
    """A run, as a caller sees it."""

    id: str
    state: HarvestState
    scope: dict[str, Any]
    adapter: dict[str, Any]
    principal: str
    mode: HarvestMode = HarvestMode.FULL
    started_at: str | None = None
    finished_at: str | None = None
    queued: int = 0
    parsed: int = 0
    skipped_unchanged: int = 0
    skipped_not_modified: int = 0
    held: int = 0
    failed: int = 0
    drifted: int = 0
    """Workbooks that changed under a Migration Unit in progress (S1.2.4)."""

    schedule_id: str | None = None
    projects: list[ProjectProgress] = field(default_factory=list)
    error: str | None = None
    parse_quality_p50: float | None = None

    @property
    def done(self) -> int:
        return (
            self.parsed
            + self.skipped_unchanged
            + self.skipped_not_modified
            + self.held
            + self.failed
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "state": self.state.value,
            "scope": self.scope,
            "adapter": self.adapter,
            "principal": self.principal,
            "mode": self.mode.value,
            "schedule_id": self.schedule_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "totals": {
                "queued": self.queued,
                "parsed": self.parsed,
                "skipped_unchanged": self.skipped_unchanged,
                "skipped_not_modified": self.skipped_not_modified,
                "held": self.held,
                "failed": self.failed,
                "drifted": self.drifted,
                "remaining": max(0, self.queued - self.done),
            },
            "parse_quality_p50": self.parse_quality_p50,
            "projects": [project.as_dict() for project in self.projects],
            "error": self.error,
        }


@dataclass(slots=True)
class WorkbookResult:
    """The outcome of one workbook, returned by the per-workbook step."""

    ref_luid: str
    project: str
    outcome: WorkbookOutcome
    parse_quality: float | None = None
    nodes_written: int = 0
    edges_written: int = 0
    failure: HarvestFailure | None = None
    drifted: bool = False
    """The source changed under a Migration Unit that was already being worked."""
