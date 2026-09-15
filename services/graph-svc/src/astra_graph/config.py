"""Service configuration.

Values come from the environment. In a deployed tenant the database credential is
projected from Azure Key Vault (spec §18.1); nothing here reads a secret from a file.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache

#: AGE graph names become PostgreSQL schema names, so the same rules apply.
_GRAPH_NAME_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


class ConfigError(Exception):
    """Configuration is missing or unusable. Raised at start-up, never at request time."""


@dataclass(frozen=True, slots=True)
class Settings:
    postgres_host: str
    postgres_port: int
    postgres_db: str
    postgres_user: str
    postgres_password: str
    graph_name: str
    env: str
    log_level: str
    pool_min_size: int
    pool_max_size: int
    scheduler_enabled: bool = True
    """Whether this process runs the harvest scheduler (S1.2.4).

    On by default, and safe with several replicas because due schedules are claimed in the
    database. Turn it off to run the API and the scheduler as separate deployments, or in a
    test that drives ``tick()`` itself.
    """

    scheduler_poll_seconds: int = 30

    target_git_repo_path: str = "/tmp/astra-target-repo"
    """Where the fixture target adapter's local Git repository lives (story S4.3.1). A
    real deployment points a Fabric-connected adapter at the client's own remote instead;
    this is the fixture's own local stand-in, the same footing the fixture source adapter
    has for harvesting."""

    target_workspace: str = "dev"
    """The single configured "dev workspace" (spec §12.2/§7.2) every approved family
    deploys into. Real per-tenant workspace configuration is E11/E12 territory; one name
    is the honest floor until a tenant has more than one dev workspace to choose between.
    """

    target_workspace_published: str = "prod"
    """Where a promoted family actually deploys (story S4.3.3) — §12.2's own "PUBLISHED:
    in test/prod" is what makes promotion more than a state flag: `promote_family` deploys
    the already-committed git ref here, a second real call to the same `TargetAdapter.
    deploy` a BUILT family's own dev deploy already used. One name, the same honest floor
    `target_workspace` already has — real dev/test/prod pipeline configuration is E9/E12's.
    """

    target_workspace_test: str = "test"
    """The real, third pipeline stage story S9.2.1 adds — until now this platform only
    ever configured two workspace names (`target_workspace`/`target_workspace_published`),
    confirmed by direct read to be the honest floor `target_workspace_published`'s own
    docstring already promised "real dev/test/prod pipeline configuration is E9/E12's" to
    build. `release.py`'s own `promote_workbook(..., to_stage="test")` deploys here —
    MA-08's own "post-G3 only" test-stage promotion, before MA-09's separate, explicitly
    PM-approved promotion to `target_workspace_published`."""

    anthropic_model: str = "claude-sonnet-5"
    """The Model Gateway's own real Anthropic provider (story S5.3.2, §5.5): the
    reasoning-tier model `AnthropicModelCaller` calls, matching §9.4's own
    `model_policy.tier: reasoning`. The key it authenticates with is not a `Settings`
    field — it comes from `CredentialProvider.resolve("anthropic/api_key")`, the same
    "a secret never crosses request-config plumbing" discipline `credentials.py` already
    established for source credentials (§18.1)."""

    entra_tenant_id: str = ""
    """Entra ID tenant GUID (spec §18.1, story S11.1.1). Empty is the honest default
    until a real tenant is connected — see entra.py's own module docstring for what
    "disclosed, not yet connected" means here. Configuring this and entra_client_id
    together is what turns on verified sign-in."""

    entra_client_id: str = ""
    """This API's own Entra ID app registration client id — the audience a bearer token
    must carry. Leaving either this or entra_tenant_id empty keeps every caller on the
    existing X-Astra-Principal/X-Astra-Roles header path, unchanged."""

    entra_group_role_map: str = ""
    """`<entra-group-id>:<role>,<entra-group-id>:<role>`... — see entra.py's own
    parse_group_role_map. Real group ids come from the client's own Entra tenant, so this
    is empty until one is connected."""

    key_vault_url: str = ""
    """`https://<vault-name>.vault.azure.net` (spec §18.1). Empty keeps credential
    resolution on EnvironmentCredentialProvider (harvest_setup.py); set it to switch to
    the real KeyVaultCredentialProvider (credentials.py) once a tenant's Key Vault
    exists."""

    bom_public_key_pem: str = ""
    """The Ed25519 public key (PEM) that verifies a deployment's signed bill of
    materials (bom.py, story S11.1.1). The private key never reaches this service — it
    is generated and held outside graph-svc (tools/generate_bom.py --generate-keypair),
    the same "a secret never crosses the API" posture credentials.py already
    established. Empty means no key is configured yet; GET /v1/deployment/bom reports
    signature_verified: null rather than pretending to have checked."""

    evidence_export_private_key_pem: str = ""
    """Story S11.3.2's own deliberate departure from `bom_public_key_pem`'s own "the
    key never reaches this service" discipline: Evidence Export must be a real,
    self-service, single action ("the console shows the signature"), which needs the
    service itself to sign at export time. Empty (the honest default for a deployment
    that has not provisioned a durable key) makes `evidence_export.py`'s own
    `LocalEvidenceSigner` generate a fresh Ed25519 key at process construction instead
    — every exported bundle carries its own public key alongside its signature (so
    verification never depends on this process's own key outliving a restart), but a
    deployment that wants one *stable* identity across restarts sets this to a real,
    durable PEM."""

    @property
    def dsn(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    def redacted_dsn(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:***"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


def _env(name: str, default: str | None = None, *, required: bool = False) -> str:
    value = os.environ.get(name, default)
    if required and not value:
        raise ConfigError(f"{name} is required")
    return value or ""


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc


def load_settings() -> Settings:
    graph_name = _env("ASTRA_GRAPH_NAME", "astra_estate")
    if not _GRAPH_NAME_RE.match(graph_name):
        # The graph name is interpolated into SQL (AGE's cypher() takes it as a literal),
        # so it is validated here rather than trusted.
        raise ConfigError(
            f"ASTRA_GRAPH_NAME must match {_GRAPH_NAME_RE.pattern}, got {graph_name!r}"
        )

    return Settings(
        postgres_host=_env("ASTRA_POSTGRES_HOST", "localhost"),
        postgres_port=_env_int("ASTRA_POSTGRES_PORT", 5432),
        postgres_db=_env("ASTRA_POSTGRES_DB", "astra"),
        postgres_user=_env("ASTRA_POSTGRES_USER", "astra"),
        postgres_password=_env("ASTRA_POSTGRES_PASSWORD", required=True),
        graph_name=graph_name,
        env=_env("ASTRA_ENV", "local"),
        log_level=_env("ASTRA_LOG_LEVEL", "INFO").upper(),
        pool_min_size=_env_int("ASTRA_PG_POOL_MIN", 2),
        pool_max_size=_env_int("ASTRA_PG_POOL_MAX", 10),
        scheduler_enabled=_env("ASTRA_SCHEDULER_ENABLED", "true").strip().lower()
        not in {"0", "false", "no"},
        scheduler_poll_seconds=_env_int("ASTRA_SCHEDULER_POLL_SECONDS", 30),
        target_git_repo_path=_env("ASTRA_TARGET_GIT_REPO_PATH", "/tmp/astra-target-repo"),
        target_workspace=_env("ASTRA_TARGET_WORKSPACE", "dev"),
        target_workspace_published=_env("ASTRA_TARGET_WORKSPACE_PUBLISHED", "prod"),
        target_workspace_test=_env("ASTRA_TARGET_WORKSPACE_TEST", "test"),
        anthropic_model=_env("ASTRA_ANTHROPIC_MODEL", "claude-sonnet-5"),
        entra_tenant_id=_env("ASTRA_ENTRA_TENANT_ID", ""),
        entra_client_id=_env("ASTRA_ENTRA_CLIENT_ID", ""),
        entra_group_role_map=_env("ASTRA_ENTRA_GROUP_ROLE_MAP", ""),
        key_vault_url=_env("ASTRA_KEY_VAULT_URL", ""),
        bom_public_key_pem=_env("ASTRA_BOM_PUBLIC_KEY_PEM", ""),
        evidence_export_private_key_pem=_env("ASTRA_EVIDENCE_EXPORT_PRIVATE_KEY_PEM", ""),
    )


@lru_cache(maxsize=1)
def settings() -> Settings:
    return load_settings()
