"""HTTP surface of graph-svc."""

from .routes import router
from .routes_adapters import router as adapters_router
from .routes_artefacts import router as artefacts_router
from .routes_classification import router as classification_router
from .routes_compositor import router as compositor_router
from .routes_conformance import router as conformance_router
from .routes_context import router as context_router
from .routes_cypher import router as cypher_router
from .routes_estate import router as estate_router
from .routes_families import router as families_router
from .routes_g2 import router as g2_router
from .routes_gateway import router as gateway_router
from .routes_generation import router as generation_router
from .routes_harvest import router as harvest_router
from .routes_lineage import router as lineage_router
from .routes_modeller import router as modeller_router
from .routes_ownership import router as ownership_router
from .routes_patterns import router as patterns_router
from .routes_platform import router as platform_router
from .routes_provenance import router as provenance_router
from .routes_quality import router as quality_router
from .routes_redesign import router as redesign_router
from .routes_rules import router as rules_router
from .routes_schedules import router as schedules_router
from .routes_trains import router as trains_router

__all__ = [
    "adapters_router",
    "artefacts_router",
    "classification_router",
    "compositor_router",
    "conformance_router",
    "context_router",
    "cypher_router",
    "estate_router",
    "families_router",
    "g2_router",
    "gateway_router",
    "generation_router",
    "harvest_router",
    "lineage_router",
    "modeller_router",
    "ownership_router",
    "patterns_router",
    "platform_router",
    "provenance_router",
    "quality_router",
    "redesign_router",
    "router",
    "rules_router",
    "schedules_router",
    "trains_router",
]
