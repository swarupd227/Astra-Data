"""graph-svc application.

The Estate Graph service (spec §5.2 ``graph-svc``): Estate Graph read/write with schema
enforcement. This story delivers the write path and the enforcement; the query API is
S1.1.2 and the mutation event stream is S1.1.3.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .adapters.conformance import PostgresConformanceStore
from .api import (
    adapters_router,
    artefacts_router,
    classification_router,
    compositor_router,
    conformance_router,
    context_router,
    cypher_router,
    estate_router,
    families_router,
    g2_router,
    gateway_router,
    generation_router,
    harvest_router,
    lineage_router,
    modeller_router,
    ownership_router,
    patterns_router,
    platform_router,
    provenance_router,
    quality_router,
    redesign_router,
    router,
    rules_router,
    schedules_router,
    trains_router,
)
from .api.graphql import build_router as build_graphql_router
from .api.routes_families import ClusteringStatus
from .api.routes_quality import DEFAULT_THRESHOLD
from .api.routes_trains import TrainProposalStatus
from .artefacts import PostgresArtefactStore
from .build import PostgresBuildStore
from .calibration import PostgresCalibrationStore
from .cartographer import Cartographer
from .classify import ClassificationEngine
from .compositor import Compositor
from .config import settings
from .conformance_rules import PostgresConformanceRulesetStore
from .context import ContextAssembler
from .errors import AstraGraphError
from .estate import EstateReader
from .events import source_for
from .g2 import PostgresQuestionStore
from .g2_reminders import LocalNotificationChannel, PostgresReminderStore
from .gateway import build_gateway
from .generation import GenerationEngine
from .grammar import LocalIssueTracker, PostgresIssueStore
from .graph import AgeGraphRepository, create_pool
from .harvest import (
    HarvestScheduler,
    PostgresHarvestStore,
    PostgresParseQualityStore,
    PostgresScheduleStore,
    Rescorer,
    TenantPromotionGate,
)
from .harvest_setup import (
    UNGATED_ADAPTERS,
    build_credential_provider,
    build_directory_resolver,
    build_harvester,
    build_migration_unit_registry,
)
from .lineage import LineageReader
from .logging_setup import configure_logging
from .modeller import Modeller
from .ontology import SCHEMA_VERSION
from .provenance import ContextVerifier, PostgresProvenanceStore
from .report_deploy import PostgresReportDeployStore
from .retention import PostgresProgrammeStore
from .rules import RulesEngine
from .scope import PostgresScopeStore
from .target_setup import build_target_adapter
from .trains import TrainPlanner
from .versions import HistoricalGraphReader
from .visual_mapping import PostgresVisualMappingRulesetStore
from .writes import GraphWriter

logger = logging.getLogger(__name__)

DESCRIPTION = """
The Estate Graph: the parsed source estate, the target artefacts as they are produced, and
the relationships between them. Every agent reads and writes it; no agent holds private
state about the estate.

Every write is checked against the ontology before it reaches the store. A write with an
unknown node or edge type, an undeclared property, a missing required property or a value
that does not match its declared type is rejected with 422 and a body naming what was
wrong.

Reads are served by a GraphQL schema generated from the same ontology at `/graphql`, and
by a read-only Cypher endpoint at `/v1/cypher` for the lineage questions the typed API has
no field for.
""".strip()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    config = settings()
    configure_logging(config.log_level)
    logger.info(
        "starting graph-svc: env=%s graph=%s schema_version=%s db=%s",
        config.env,
        config.graph_name,
        SCHEMA_VERSION,
        config.redacted_dsn(),
    )
    pool = await create_pool(config)
    repository = AgeGraphRepository(pool, graph_name=config.graph_name)
    writer = GraphWriter(repository, event_source=source_for(config.graph_name))
    app.state.pool = pool
    app.state.repository = repository
    app.state.writer = writer
    harvest_store = PostgresHarvestStore(pool, graph_name=config.graph_name)
    quality_store = PostgresParseQualityStore(pool)
    app.state.harvest_store = harvest_store
    app.state.quality_store = quality_store
    app.state.harvest_tasks = set()
    app.state.schedule_store = PostgresScheduleStore(pool, graph_name=config.graph_name)
    # The directory resolver is E11's; until then nothing resolves and every owner is
    # listed as unresolved, which is what is true of a platform that cannot reach one.
    app.state.directory = build_directory_resolver(config)
    # Migration Units arrive with the Cartographer (E3). Until then nothing has work in
    # progress over a workbook, so no harvest can disturb any.
    app.state.migration_units = build_migration_unit_registry(config)
    # Before the Harvester, which holds the promotion gate that reads it (S2.1.2). Wired
    # here rather than with the other stores below because the ordering is a dependency and
    # not a preference — an earlier arrangement of these lines failed at start-up, and the
    # unit suite could not see it because those tests set app state directly.
    app.state.conformance_store = PostgresConformanceStore(pool, graph_name=config.graph_name)

    app.state.harvester = build_harvester(
        config,
        writer=writer,
        store=harvest_store,
        quality=quality_store,
        directory=app.state.directory,
        migration_units=app.state.migration_units,
        # S2.1.2: an adapter this tenant has not promoted may not harvest its estate. The
        # fixture adapter is exempt and the exemption is a named constant, not a flag.
        promotions=TenantPromotionGate(
            app.state.conformance_store, exempt=UNGATED_ADAPTERS
        ),
    )
    # One assembler for every agent (spec §4.1.3). It is told which adapter's grammar
    # produced the ASTs in this graph, because a pattern's signature declares the adapter
    # it was written for and a Tableau pattern must not be offered for another source.
    adapter_name = (
        app.state.harvester.manifest().name if app.state.harvester is not None else None
    )
    app.state.assembler = ContextAssembler(repository, adapter=adapter_name)

    # S1.3.2. The same assembler, over the graph as it stood at an event offset. Building
    # it from a historical reader rather than a second code path is the whole reason a
    # re-materialised context is evidence: if audit used different code, it would prove
    # something about that code rather than about what the agent saw.
    async def assembler_at(version: int) -> ContextAssembler:
        return ContextAssembler(
            HistoricalGraphReader(pool, graph_name=config.graph_name, version=version),
            adapter=adapter_name,
        )

    async def current_version() -> int:
        version, _at = await repository.current_version()
        return version

    app.state.estate_reader = EstateReader(pool, graph_name=config.graph_name)
    app.state.lineage_reader = LineageReader(pool, graph_name=config.graph_name)
    app.state.scope_store = PostgresScopeStore(pool, graph_name=config.graph_name)
    app.state.issue_store = PostgresIssueStore(pool, graph_name=config.graph_name)
    # Work tracking is optional and one-way (§21); the ADO/Jira mirror is R1.1. Until
    # then an issue is held here and the API says so rather than pretending to file it.
    app.state.issue_tracker = LocalIssueTracker()
    app.state.provenance_store = PostgresProvenanceStore(pool, graph_name=config.graph_name)
    app.state.artefact_store = PostgresArtefactStore(pool, graph_name=config.graph_name)
    app.state.programme_store = PostgresProgrammeStore(pool, graph_name=config.graph_name)
    app.state.cartographer = Cartographer(
        pool, graph_name=config.graph_name, writer=writer, programme_store=app.state.programme_store
    )
    app.state.cartographer_status = ClusteringStatus()
    app.state.train_planner = TrainPlanner(
        pool, graph_name=config.graph_name, writer=writer, scope_store=app.state.scope_store
    )
    app.state.train_status = TrainProposalStatus()
    app.state.modeller = Modeller(
        pool,
        graph_name=config.graph_name,
        writer=writer,
        provenance_store=app.state.provenance_store,
    )
    app.state.question_store = PostgresQuestionStore(pool, graph_name=config.graph_name)
    app.state.reminder_store = PostgresReminderStore(pool, graph_name=config.graph_name)
    # No outward channel is wired yet (§21's own posture, extended to notifications by
    # story S4.2.2) — a reminder is recorded and logged here rather than claiming delivery
    # nobody could verify.
    app.state.notification_channel = LocalNotificationChannel()
    app.state.build_store = PostgresBuildStore(pool, graph_name=config.graph_name)
    # Story S4.3.2: the architect's own saved rules, versioned; a fresh graph builds
    # against the in-memory default (version 0) until an architect saves one of their own.
    app.state.conformance_store = PostgresConformanceRulesetStore(pool, graph_name=config.graph_name)
    # No live Fabric tenant is configured anywhere this platform has been deployed yet
    # (story S4.3.1) — the fixture target adapter's own docstring says what stands in for
    # what, the identical "real until later" posture the source side's fixture has had
    # since before F2.2.
    app.state.target_adapter = build_target_adapter(config)
    app.state.target_workspace = config.target_workspace
    app.state.target_workspace_published = config.target_workspace_published
    # Story S5.1.1: the Transpiler's own first piece, classification — reads Appendix B.1's
    # families straight off the AST the Tableau grammar already stamps, no new store.
    app.state.classifier = ClassificationEngine(
        pool, graph_name=config.graph_name, writer=writer, provenance_store=app.state.provenance_store,
    )
    # Story S5.2.1: the deterministic rules engine — C1/C2 calculations rendered into real
    # DAX, with real provenance, no model call.
    app.state.rules_engine = RulesEngine(
        pool, graph_name=config.graph_name, writer=writer, provenance_store=app.state.provenance_store,
    )
    # Story S5.3.2: the real Model Gateway (§5.5) — a real Anthropic provider behind a
    # task-class router, gated by a real, Postgres-backed eval-set policy. Registering a
    # provider here does not make it routable; a platform engineer runs
    # POST /v1/model-gateway:run-eval to earn that.
    app.state.gateway = build_gateway(
        config, pool=pool, graph_name=config.graph_name,
        credentials=build_credential_provider(config),
    )
    # Story S5.3.3: real confidence calibration (§16.3) — every declared confidence is
    # recorded, win or lose, and a task class whose own history falls below the floor is
    # routed to the (disclosed-absent) small-model-plus-proof path rather than trusted.
    app.state.calibration = PostgresCalibrationStore(pool, graph_name=config.graph_name)
    # Story S5.3.1: Class 3 calculations, generated behind the §16.1 validation ladder,
    # calling the Model Gateway above for a candidate — never naming a provider itself.
    app.state.generation_engine = GenerationEngine(
        pool, graph_name=config.graph_name, writer=writer, provenance_store=app.state.provenance_store,
        gateway=app.state.gateway, calibration=app.state.calibration,
    )
    # Story S6.1.1: the Compositor, E6's first piece -- each Tableau sheet mapped to a
    # Power BI visual, bound through MAPS_TO, PBIR-validated before anything is written.
    # A fresh graph builds against the in-memory default mapping table (version 0) until an
    # architect saves one of their own, the identical posture `conformance_store` already has.
    app.state.visual_mapping_store = PostgresVisualMappingRulesetStore(pool, graph_name=config.graph_name)
    app.state.compositor = Compositor(pool, graph_name=config.graph_name, writer=writer)
    # Story S6.1.2: commit and deploy a composed report through the same target adapter
    # `build_family` already uses — reused verbatim, no second commit/deploy mechanism.
    app.state.report_deploy_store = PostgresReportDeployStore(pool, graph_name=config.graph_name)
    app.state.verifier = ContextVerifier(assembler_at, current_version=current_version)
    app.state.rescorer = Rescorer(
        quality=quality_store,
        counts=harvest_store,
        writer=writer,
        graph_name=config.graph_name,
        threshold=DEFAULT_THRESHOLD,
    )
    # S1.2.4. One scheduler per process; due schedules are claimed in the database, so
    # replicas share the work rather than each running all of it.
    app.state.scheduler = None
    scheduler_task: asyncio.Task[None] | None = None
    if app.state.harvester is None:
        logger.info("no source adapter, so no harvest scheduler started")
    elif not config.scheduler_enabled:
        logger.info("harvest scheduler disabled by configuration")
    else:
        app.state.scheduler = HarvestScheduler(
            store=app.state.schedule_store,
            harvester=app.state.harvester,
            poll_seconds=config.scheduler_poll_seconds,
        )
        scheduler_task = asyncio.create_task(app.state.scheduler.run_forever())

    try:
        yield
    finally:
        if scheduler_task is not None:
            scheduler_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await scheduler_task
        # A harvest is an in-process task until Temporal takes over (E12/F12.1). Its run
        # record is already persisted, so cancelling here loses the worker, not the trail.
        for task in list(app.state.harvest_tasks):
            task.cancel()
        await pool.close()
        logger.info("graph-svc stopped")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Astra Data — Estate Graph",
        version=f"schema-{SCHEMA_VERSION}",
        description=DESCRIPTION,
        lifespan=lifespan,
    )

    @app.exception_handler(AstraGraphError)
    async def handle_astra_error(_: Request, exc: AstraGraphError) -> JSONResponse:
        if exc.status_code >= 500:
            logger.exception("request failed: %s", exc)
        return JSONResponse(status_code=exc.status_code, content=exc.payload())

    app.include_router(router)
    app.include_router(cypher_router)
    app.include_router(harvest_router)
    app.include_router(quality_router)
    app.include_router(ownership_router)
    app.include_router(schedules_router)
    app.include_router(platform_router)
    app.include_router(context_router)
    app.include_router(provenance_router)
    app.include_router(estate_router)
    app.include_router(lineage_router)
    app.include_router(adapters_router)
    app.include_router(artefacts_router)
    app.include_router(families_router)
    app.include_router(g2_router)
    app.include_router(trains_router)
    app.include_router(modeller_router)
    app.include_router(conformance_router)
    app.include_router(classification_router)
    app.include_router(redesign_router)
    app.include_router(rules_router)
    app.include_router(generation_router)
    app.include_router(patterns_router)
    app.include_router(gateway_router)
    app.include_router(compositor_router)
    app.include_router(build_graphql_router(), prefix="/graphql", tags=["query"])
    return app


app = create_app()
