"""HTTP surface of graph-svc."""

from .routes import router
from .routes_adapters import router as adapters_router
from .routes_adoption import router as adoption_router
from .routes_artefacts import router as artefacts_router
from .routes_calibration_wave import router as calibration_wave_router
from .routes_case_derivation import router as case_derivation_router
from .routes_case_execution import router as case_execution_router
from .routes_classification import router as classification_router
from .routes_compositor import router as compositor_router
from .routes_conformance import router as conformance_router
from .routes_context import router as context_router
from .routes_cypher import router as cypher_router
from .routes_data_handling import router as data_handling_router
from .routes_decision_register import router as decision_register_router
from .routes_deployment_bom import router as deployment_bom_router
from .routes_estate import router as estate_router
from .routes_events_stream import router as events_stream_router
from .routes_evidence_chain import router as evidence_chain_router
from .routes_evidence_export import router as evidence_export_router
from .routes_exceptions import router as exceptions_router
from .routes_execution_safety import router as execution_safety_router
from .routes_explain import router as explain_router
from .routes_failure_classification import router as failure_classification_router
from .routes_families import router as families_router
from .routes_g2 import router as g2_router
from .routes_g3 import router as g3_router
from .routes_g4 import router as g4_router
from .routes_gate_inbox import router as gate_inbox_router
from .routes_gateway import router as gateway_router
from .routes_generation import router as generation_router
from .routes_harvest import router as harvest_router
from .routes_lineage import router as lineage_router
from .routes_mender import router as mender_router
from .routes_modeller import router as modeller_router
from .routes_mu_page import router as mu_page_router
from .routes_mu_workflow import router as mu_workflow_router
from .routes_notifications import router as notifications_router
from .routes_scheduler import router as scheduler_router
from .routes_ownership import router as ownership_router
from .routes_patterns import router as patterns_router
from .routes_platform import router as platform_router
from .routes_programme_surface import router as programme_surface_router
from .routes_provenance import router as provenance_router
from .routes_quality import router as quality_router
from .routes_rebuild import router as rebuild_router
from .routes_redesign import router as redesign_router
from .routes_regression import router as regression_router
from .routes_release import router as release_router
from .routes_rules import router as rules_router
from .routes_schedules import router as schedules_router
from .routes_status_pack import router as status_pack_router
from .routes_tenant_access import router as tenant_access_router
from .routes_tolerance_charter import router as tolerance_charter_router
from .routes_trains import router as trains_router
from .routes_verdicts import router as verdicts_router
from .routes_visual_parity import router as visual_parity_router

__all__ = [
    "adapters_router",
    "adoption_router",
    "artefacts_router",
    "calibration_wave_router",
    "case_derivation_router",
    "case_execution_router",
    "classification_router",
    "compositor_router",
    "conformance_router",
    "context_router",
    "cypher_router",
    "data_handling_router",
    "decision_register_router",
    "deployment_bom_router",
    "estate_router",
    "events_stream_router",
    "evidence_chain_router",
    "evidence_export_router",
    "exceptions_router",
    "execution_safety_router",
    "explain_router",
    "failure_classification_router",
    "families_router",
    "g2_router",
    "g3_router",
    "g4_router",
    "gate_inbox_router",
    "gateway_router",
    "generation_router",
    "harvest_router",
    "lineage_router",
    "mender_router",
    "modeller_router",
    "mu_page_router",
    "mu_workflow_router",
    "notifications_router",
    "ownership_router",
    "patterns_router",
    "platform_router",
    "programme_surface_router",
    "provenance_router",
    "quality_router",
    "rebuild_router",
    "redesign_router",
    "regression_router",
    "release_router",
    "router",
    "rules_router",
    "scheduler_router",
    "schedules_router",
    "status_pack_router",
    "tenant_access_router",
    "tolerance_charter_router",
    "trains_router",
    "verdicts_router",
    "visual_parity_router",
]
