"""The real Temporal worker process -- story S12.1.1, opening E12/F12.1.

Spec §5.2's own component table names `orchestrator | workflow engine | ... | cluster`
as its own deployable, distinct from `graph-svc` itself. This module is that
deployable's own entry point (`python -m astra_graph.mu_worker`) -- a second real
process, not folded into the FastAPI app's own lifespan, matching the spec's own
"a separate pod" framing and the Helm chart's own already-real `temporal.enabled`
dependency this story finally has a client for.

Builds the identical real collaborators `main.py`'s own lifespan already builds for
the HTTP-driven Transpiler/Mender routes (`build_gateway`, `build_target_adapter`,
the real Postgres-backed stores) and binds them into one `MuActivities` instance, so
an activity's own real work is byte-identical to what the same call already does when
triggered over HTTP -- this worker adds durability and gate-wait signalling, not a
second implementation of the agent runs themselves.
"""

from __future__ import annotations

import asyncio
import logging

from temporalio.client import Client
from temporalio.worker import Worker

from .artefacts import PostgresArtefactStore
from .calibration import PostgresCalibrationStore
from .config import settings
from .events import source_for
from .gateway import build_gateway
from .graph import AgeGraphRepository, create_pool
from .harvest_setup import build_credential_provider
from .logging_setup import configure_logging
from .mender import PostgresMenderConfigStore
from .mu_workflow import MigrationUnitWorkflow, MuActivities
from .provenance import PostgresProvenanceStore
from .target_setup import build_target_adapter
from .tolerance_charter import PostgresToleranceCharterStore
from .writes import GraphWriter

#: One real, fixed queue this story's worker polls -- a second workflow type sharing
#: it would need its own task-routing decision this story does not need to make yet.
TASK_QUEUE = "mu-workflow"

logger = logging.getLogger(__name__)


async def run_worker() -> None:
    config = settings()
    configure_logging(config.log_level)
    logger.info("starting mu-workflow worker: env=%s graph=%s", config.env, config.graph_name)

    pool = await create_pool(config)
    repository = AgeGraphRepository(pool, graph_name=config.graph_name)
    writer = GraphWriter(repository, event_source=source_for(config.graph_name))

    activities = MuActivities(
        pool=pool,
        graph_name=config.graph_name,
        writer=writer,
        provenance_store=PostgresProvenanceStore(pool, graph_name=config.graph_name),
        artefact_store=PostgresArtefactStore(pool, graph_name=config.graph_name),
        gateway=build_gateway(
            config, pool=pool, graph_name=config.graph_name,
            credentials=build_credential_provider(config),
        ),
        target_adapter=build_target_adapter(config),
        config_store=PostgresMenderConfigStore(pool, graph_name=config.graph_name),
        charter_store=PostgresToleranceCharterStore(pool, graph_name=config.graph_name),
        calibration_store=PostgresCalibrationStore(pool, graph_name=config.graph_name),
    )

    client = await Client.connect(config.temporal_address, namespace=config.temporal_namespace)
    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[MigrationUnitWorkflow],
        activities=[activities.write_mu_state, activities.run_generate, activities.run_mend],
    )
    logger.info("mu-workflow worker ready: task_queue=%s temporal=%s", TASK_QUEUE, config.temporal_address)
    await worker.run()


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
