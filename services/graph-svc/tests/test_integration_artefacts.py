"""The artefact store against real PostgreSQL — S2.4.2.

What only a real database can be asked about: that the ``bytea`` column round-trips a PNG
byte-for-byte, that content addressing survives the trip, and that the MU index actually
narrows a listing rather than scanning everything and happening to agree in-memory.
"""

from __future__ import annotations

import hashlib
import os

import pytest

pytestmark = pytest.mark.integration

asyncpg = pytest.importorskip("asyncpg")

from astra_graph.artefacts import ArtefactError, PostgresArtefactStore  # noqa: E402
from astra_graph.config import Settings  # noqa: E402
from astra_graph.graph import create_pool  # noqa: E402
from astra_graph.ids import new_ulid  # noqa: E402

PRINCIPAL = "agent:harvester"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
IMAGE = PNG_MAGIC + b"\x00" * 512 + b"an integration test's stand-in for a real render"


def _settings() -> Settings:
    return Settings(
        postgres_host=os.environ.get("ASTRA_POSTGRES_HOST", "localhost"),
        postgres_port=int(os.environ.get("ASTRA_POSTGRES_PORT", "5432")),
        postgres_db=os.environ.get("ASTRA_POSTGRES_DB", "astra"),
        postgres_user=os.environ.get("ASTRA_POSTGRES_USER", "astra"),
        postgres_password=os.environ.get("ASTRA_POSTGRES_PASSWORD", "astra_local_dev_only"),
        graph_name=os.environ.get("ASTRA_GRAPH_NAME", "astra_estate_test"),
        env="test",
        log_level="WARNING",
        pool_min_size=1,
        pool_max_size=4,
    )


@pytest.fixture(scope="module")
async def settings() -> Settings:
    config = _settings()
    try:
        conn = await asyncpg.connect(dsn=config.dsn, timeout=3)
    except Exception as exc:
        pytest.skip(f"PostgreSQL with Apache AGE not reachable: {exc}")
    await conn.close()
    return config


@pytest.fixture
async def store(settings: Settings):
    pool = await create_pool(settings)
    try:
        yield PostgresArtefactStore(pool, graph_name=settings.graph_name)
    finally:
        await pool.close()


def mu_ref() -> str:
    # Integration tests share a graph and a database (a known trap — see the harvest suite's
    # own notes): the random tail, never `new_ulid()[:8]`, which is the millisecond
    # timestamp and collides within a fast-running suite.
    return f"wb-{new_ulid()[-10:].lower()}"


async def test_the_bytes_survive_the_database_byte_for_byte(store) -> None:
    """The ``bytea`` round trip, which only a real database can be asked about."""
    record = await store.store(
        kind="visual_capture",
        mu_ref=mu_ref(),
        case_id="v1",
        content=IMAGE,
        media_type="image/png",
        width=800,
        height=600,
        adapter_name="tableau",
        adapter_version="0.1.0",
        interface_version="1.1",
        created_by=PRINCIPAL,
    )

    fetched = await store.content(record.id)

    assert fetched == IMAGE
    assert fetched is not None and fetched.startswith(PNG_MAGIC)


async def test_the_content_hash_matches_what_was_stored(store) -> None:
    record = await store.store(
        kind="visual_capture", mu_ref=mu_ref(), case_id="v1", content=IMAGE,
        media_type="image/png", created_by=PRINCIPAL,
    )

    assert record.content_hash == hashlib.sha256(IMAGE).hexdigest()

    read = await store.get(record.id)
    assert read is not None
    assert read.content_hash == record.content_hash
    assert read.size_bytes == len(IMAGE)


async def test_the_mu_index_narrows_a_listing(store) -> None:
    """The index this story asks for by name: "linked to the MU". A store that scanned
    every artefact in the tenant and filtered in Python would still pass a smaller test; this
    one asks the database to do it, against rows real enough to make a bad plan slow."""
    this_mu = mu_ref()
    other_mu = mu_ref()

    for index in range(4):
        await store.store(
            kind="visual_capture", mu_ref=this_mu, case_id=f"v{index}", content=IMAGE,
            media_type="image/png", created_by=PRINCIPAL,
        )
    await store.store(
        kind="visual_capture", mu_ref=other_mu, case_id="v0", content=IMAGE,
        media_type="image/png", created_by=PRINCIPAL,
    )

    listed = await store.for_mu(this_mu)

    assert len(listed) == 4
    assert {record.mu_ref for record in listed} == {this_mu}
    assert all(record.recorded_at is not None for record in listed)


async def test_tenants_do_not_see_each_others_artefacts(settings: Settings) -> None:
    """The ``graph`` column is tenant scoping, the same as every other store in this
    service. A store built against a different graph name must not find this one's rows."""
    pool = await create_pool(settings)
    try:
        mine = PostgresArtefactStore(pool, graph_name=settings.graph_name)
        elsewhere = PostgresArtefactStore(pool, graph_name=f"other-{new_ulid()[-8:].lower()}")

        record = await mine.store(
            kind="visual_capture", mu_ref=mu_ref(), case_id="v0", content=IMAGE,
            media_type="image/png", created_by=PRINCIPAL,
        )

        assert await elsewhere.get(record.id) is None
        assert await elsewhere.content(record.id) is None
    finally:
        await pool.close()


async def test_an_artefact_with_no_bytes_is_refused(store) -> None:
    with pytest.raises(ArtefactError):
        await store.store(
            kind="visual_capture", mu_ref=mu_ref(), case_id="v0", content=b"",
            media_type="image/png", created_by=PRINCIPAL,
        )
