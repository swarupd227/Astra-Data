"""Confidence calibration, against real PostgreSQL — story S5.3.3.

What only the real store can answer: that `PostgresCalibrationStore` genuinely persists an
observation and reads it back through the same ten-bucket aggregation `test_calibration.py`
already proves correct in isolation, and that `is_below_floor` reflects real accumulated
history rather than a single call's own in-memory state — none of which the pure-function
tests (`build_report` fed a literal list) can see.
"""

from __future__ import annotations

import asyncio
import os
import threading
from collections.abc import Callable, Coroutine
from typing import Any

import pytest

pytestmark = pytest.mark.integration

asyncpg = pytest.importorskip("asyncpg")

from astra_graph.calibration import (  # noqa: E402
    DEFAULT_CALIBRATION_FLOOR,
    MIN_OBSERVATIONS_FOR_FLOOR_CHECK,
    PostgresCalibrationStore,
)
from astra_graph.config import Settings  # noqa: E402
from astra_graph.gateway import TRANSPILE_C3  # noqa: E402
from astra_graph.graph import create_pool  # noqa: E402
from astra_graph.ids import new_ulid  # noqa: E402
from astra_graph.migrations import run as run_migrations  # noqa: E402
from astra_graph.principal import Principal  # noqa: E402

PRINCIPAL = Principal("user:platform@artizent.example")


def _settings(graph_name: str) -> Settings:
    return Settings(
        postgres_host=os.environ.get("ASTRA_POSTGRES_HOST", "localhost"),
        postgres_port=int(os.environ.get("ASTRA_POSTGRES_PORT", "5432")),
        postgres_db=os.environ.get("ASTRA_POSTGRES_DB", "astra"),
        postgres_user=os.environ.get("ASTRA_POSTGRES_USER", "astra"),
        postgres_password=os.environ.get("ASTRA_POSTGRES_PASSWORD", "astra_local_dev_only"),
        graph_name=graph_name,
        env="test",
        log_level="WARNING",
        pool_min_size=1,
        pool_max_size=6,
        scheduler_enabled=False,
    )


def _run_off_loop(factory: Callable[[], Coroutine[Any, Any, Any]]) -> Any:
    box: dict[str, Any] = {}

    def runner() -> None:
        try:
            box["value"] = asyncio.run(factory())
        except BaseException as exc:
            box["error"] = exc

    thread = threading.Thread(target=runner)
    thread.start()
    thread.join()
    if "error" in box:
        raise box["error"]
    return box.get("value")


@pytest.fixture(scope="module")
def settings() -> Settings:
    # No Apache AGE graph is created here -- like test_integration_gateway.py's own policy
    # store tests, PostgresCalibrationStore touches only the platform table
    # `public.calibration_observation` (migration v0022); `graph_name` is a scoping column,
    # not an AGE graph this test ever reads or writes as a graph.
    config = _settings(f"astra_calibration_{new_ulid()[10:22].lower()}")

    async def setup() -> bool:
        try:
            conn = await asyncpg.connect(dsn=config.dsn, timeout=3)
        except Exception:
            return False
        try:
            await run_migrations(conn)
        finally:
            await conn.close()
        return True

    async def teardown() -> None:
        conn = await asyncpg.connect(dsn=config.dsn)
        try:
            await conn.execute(
                "DELETE FROM public.calibration_observation WHERE graph = $1", config.graph_name
            )
        finally:
            await conn.close()

    if not _run_off_loop(setup):
        pytest.skip("PostgreSQL with Apache AGE not reachable")

    yield config
    _run_off_loop(teardown)


@pytest.fixture
async def store(settings: Settings):
    pool = await create_pool(settings)
    try:
        yield PostgresCalibrationStore(pool, graph_name=settings.graph_name)
    finally:
        await pool.close()


async def test_an_unrecorded_task_class_reports_no_observations(store) -> None:
    report = await store.report("nobody_has_ever_scored_this_task_class")
    assert report.total_observations == 0
    assert report.below_floor is False


async def test_a_recorded_observation_round_trips_into_the_right_bucket(store) -> None:
    await store.record(
        task_class=TRANSPILE_C3, agent="transpiler", model="claude-sonnet-5",
        provider="anthropic", confidence=0.42, observed_pass=True, created_by=PRINCIPAL.value,
    )
    report = await store.report(TRANSPILE_C3)
    assert report.total_observations >= 1
    bucket = report.buckets[4]  # [0.4, 0.5)
    assert bucket.count >= 1
    assert bucket.observed_pass_rate == 1.0


async def test_is_below_floor_reflects_real_accumulated_history(store) -> None:
    task_class = f"transpile_c3_test_{new_ulid()[10:18].lower()}"
    assert await store.is_below_floor(task_class) is False  # no history yet

    for _ in range(MIN_OBSERVATIONS_FOR_FLOOR_CHECK):
        await store.record(
            task_class=task_class, agent="transpiler", model="claude-sonnet-5",
            provider="anthropic", confidence=0.9, observed_pass=False, created_by=PRINCIPAL.value,
        )

    assert await store.is_below_floor(task_class) is True
    assert await store.is_below_floor(task_class, floor=0.0) is False  # a floor of 0 is never crossed


async def test_a_second_task_class_history_is_isolated_from_the_first(store) -> None:
    task_a = f"transpile_c3_test_a_{new_ulid()[10:18].lower()}"
    task_b = f"transpile_c3_test_b_{new_ulid()[10:18].lower()}"
    for _ in range(MIN_OBSERVATIONS_FOR_FLOOR_CHECK):
        await store.record(
            task_class=task_a, agent="transpiler", model="claude-sonnet-5",
            provider="anthropic", confidence=0.9, observed_pass=False, created_by=PRINCIPAL.value,
        )
    assert await store.is_below_floor(task_a) is True
    assert await store.is_below_floor(task_b) is False


async def test_report_carries_the_default_floor_when_none_is_given(store) -> None:
    report = await store.report(TRANSPILE_C3)
    assert report.floor == DEFAULT_CALIBRATION_FLOOR
