"""The Harvester (spec §8.4, story S1.2.1)."""

from .identity import derive_id, source_key
from .model import (
    HarvestFailure,
    HarvestMode,
    HarvestProgress,
    HarvestState,
    ProjectProgress,
    WorkbookOutcome,
    WorkbookResult,
)
from .promotion import (
    AdapterNotPromoted,
    PromotionGate,
    TenantPromotionGate,
    UngatedPromotions,
)
from .quality import (
    Construct,
    ConstructGroup,
    HeldWorkbook,
    InMemoryParseQualityStore,
    ParseQualityStore,
    PostgresParseQualityStore,
    Rescore,
    score,
)
from .rescore import Rescorer, RescoreResult
from .runner import (
    DEFAULT_CONCURRENCY,
    DEFAULT_PARSE_QUALITY_THRESHOLD,
    DEFAULT_USAGE_WINDOW_DAYS,
    Harvester,
    HarvestRequest,
)
from .schedule import (
    MAX_INTERVAL_MINUTES,
    MIN_INTERVAL_MINUTES,
    Cadence,
    InMemoryScheduleStore,
    PostgresScheduleStore,
    Schedule,
    ScheduleError,
    ScheduleStore,
    new_schedule,
)
from .scheduler import (
    DEFAULT_POLL_SECONDS,
    MAX_CONSECUTIVE_FAILURES,
    SCHEDULER_PRINCIPAL,
    HarvestScheduler,
)
from .store import HarvestStore, InMemoryHarvestStore, PostgresHarvestStore, WorkbookState

__all__ = [
    "DEFAULT_CONCURRENCY",
    "DEFAULT_PARSE_QUALITY_THRESHOLD",
    "DEFAULT_POLL_SECONDS",
    "DEFAULT_USAGE_WINDOW_DAYS",
    "MAX_CONSECUTIVE_FAILURES",
    "MAX_INTERVAL_MINUTES",
    "MIN_INTERVAL_MINUTES",
    "SCHEDULER_PRINCIPAL",
    "AdapterNotPromoted",
    "Cadence",
    "Construct",
    "ConstructGroup",
    "HarvestFailure",
    "HarvestMode",
    "HarvestProgress",
    "HarvestRequest",
    "HarvestScheduler",
    "HarvestState",
    "HarvestStore",
    "Harvester",
    "HeldWorkbook",
    "InMemoryHarvestStore",
    "InMemoryParseQualityStore",
    "InMemoryScheduleStore",
    "ParseQualityStore",
    "PostgresHarvestStore",
    "PostgresParseQualityStore",
    "PostgresScheduleStore",
    "ProjectProgress",
    "PromotionGate",
    "Rescore",
    "RescoreResult",
    "Rescorer",
    "Schedule",
    "ScheduleError",
    "ScheduleStore",
    "TenantPromotionGate",
    "UngatedPromotions",
    "WorkbookOutcome",
    "WorkbookResult",
    "WorkbookState",
    "derive_id",
    "new_schedule",
    "score",
    "source_key",
]
