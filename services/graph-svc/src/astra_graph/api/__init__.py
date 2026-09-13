"""HTTP surface of graph-svc."""

from .routes import router
from .routes_adapters import router as adapters_router
from .routes_adoption import router as adoption_router
from .routes_artefacts import router as artefacts_router
from .routes_case_derivation import router as case_derivation_router
from .routes_case_execution import router as case_execution_router
from .routes_classification import router as classification_router
from .routes_compositor import router as compositor_router
from .routes_conformance import router as conformance_router
from .routes_context import router as context_router
from .routes_cypher import router as cypher_router
from .routes_estate import router as estate_router
from .routes_exceptions import router as exceptions_router
from .routes_failure_classification import router as failure_classification_router
from .routes_families import router as families_router
from .routes_g2 import router as g2_router
from .routes_g3 import router as g3_router
from .routes_g4 import router as g4_router
from .routes_gateway import router as gateway_router
from .routes_generation import router as generation_router
from .routes_harvest import router as harvest_router
from .routes_lineage import router as lineage_router
from .routes_mender import router as mender_router
from .routes_modeller import router as modeller_router
from .routes_ownership import router as ownership_router
from .routes_patterns import router as patterns_router
from .routes_platform import router as platform_router
from .routes_provenance import router as provenance_router
from .routes_quality import router as quality_router
from .routes_redesign import router as redesign_router
from .routes_regression import router as regression_router
from .routes_release import router as release_router
from .routes_rules import router as rules_router
from .routes_schedules import router as schedules_router
from .routes_tolerance_charter import router as tolerance_charter_router
from .routes_trains import router as trains_router
from .routes_verdicts import router as verdicts_router
from .routes_visual_parity import router as visual_parity_router

__all__ = [
    "adapters_router",
    "adoption_router",
    "artefacts_router",
    "case_derivation_router",
    "case_execution_router",
    "classification_router",
    "compositor_router",
    "conformance_router",
    "context_router",
    "cypher_router",
    "estate_router",
    "exceptions_router",
    "failure_classification_router",
    "families_router",
    "g2_router",
    "g3_router",
    "g4_router",
    "gateway_router",
    "generation_router",
    "harvest_router",
    "lineage_router",
    "mender_router",
    "modeller_router",
    "ownership_router",
    "patterns_router",
    "platform_router",
    "provenance_router",
    "quality_router",
    "redesign_router",
    "regression_router",
    "release_router",
    "router",
    "rules_router",
    "schedules_router",
    "tolerance_charter_router",
    "trains_router",
    "verdicts_router",
    "visual_parity_router",
]
