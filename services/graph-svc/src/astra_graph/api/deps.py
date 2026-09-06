"""Request-scoped dependencies."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import Depends, Header, HTTPException, Request, status

from ..errors import ForbiddenError
from ..graph import GraphRepository
from ..observability import QueryLog
from ..principal import (
    PRINCIPAL_HEADER,
    RUN_HEADER,
    InvalidPrincipalError,
    Principal,
    parse,
)
from ..roles import ROLES_HEADER, InvalidRolesError, Role, RoleSet
from ..roles import parse as parse_roles
from ..writes import GraphWriter

if TYPE_CHECKING:  # A real import would close a cycle; see contract.py's _schema().
    from ..context import ContextAssembler


def get_writer(request: Request) -> GraphWriter:
    writer: GraphWriter | None = getattr(request.app.state, "writer", None)
    if writer is None:  # pragma: no cover - only reachable if start-up failed
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="graph store is not ready",
        )
    return writer


def get_repository(request: Request) -> GraphRepository:
    repository: GraphRepository | None = getattr(request.app.state, "repository", None)
    if repository is None:  # pragma: no cover
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="graph store is not ready",
        )
    return repository


def get_assembler(request: Request) -> ContextAssembler:
    """The shared context assembler (spec §4.1.3).

    Shared deliberately: one object canonicalises, hashes, measures and refuses for every
    agent, so those four cannot drift apart between them.
    """
    assembler: ContextAssembler | None = getattr(request.app.state, "assembler", None)
    if assembler is None:  # pragma: no cover
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="the context assembler is not ready",
        )
    return assembler


def get_principal(
    principal: Annotated[str | None, Header(alias=PRINCIPAL_HEADER)] = None,
    run_id: Annotated[str | None, Header(alias=RUN_HEADER)] = None,
) -> Principal:
    try:
        return parse(principal, run_id)
    except InvalidPrincipalError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "invalid_principal", "message": str(exc)},
        ) from exc


def get_role_set(
    roles: Annotated[str | None, Header(alias=ROLES_HEADER)] = None,
) -> RoleSet:
    try:
        return parse_roles(roles)
    except InvalidRolesError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "invalid_roles", "message": str(exc)},
        ) from exc


WriterDep = Annotated[GraphWriter, Depends(get_writer)]
RepositoryDep = Annotated[GraphRepository, Depends(get_repository)]
PrincipalDep = Annotated[Principal, Depends(get_principal)]
RoleSetDep = Annotated[RoleSet, Depends(get_role_set)]


def require_artizent(roles: RoleSetDep) -> RoleSet:
    """Gate an endpoint on the delivery organisation (spec §2.4).

    The raw Cypher endpoint bypasses the shaping the console applies to client surfaces,
    so it is open to Artizent roles and closed to client roles.
    """
    if not roles.is_artizent():
        raise ForbiddenError(
            f"this endpoint is available to Artizent roles; declare one in {ROLES_HEADER}"
        )
    return roles


ArtizentDep = Annotated[RoleSet, Depends(require_artizent)]


def require_programme_manager(roles: RoleSetDep) -> RoleSet:
    """Gate an endpoint on the role that owns scope (spec §15.1).

    Re-tiering and withdrawing from scope change what the programme has committed to
    deliver. S1.4.1 puts both behind the Programme Manager, and §15.2 requires each to
    carry a reason — so the gate and the reason are enforced together, at the API, rather
    than by a console that only hides the button.
    """
    if Role.PROGRAMME_MANAGER not in roles.roles:
        raise ForbiddenError(
            f"changing programme scope is the Programme Manager's; declare "
            f"'{Role.PROGRAMME_MANAGER.value}' in {ROLES_HEADER}"
        )
    return roles


ProgrammeManagerDep = Annotated[RoleSet, Depends(require_programme_manager)]


def require_semantic_model_engineer(roles: RoleSetDep) -> RoleSet:
    """Gate an endpoint on the role that owns model design (spec §8.6, §15.1).

    Generating a model design proposal is Modeller work a Semantic Model Engineer starts
    from and edits (backlog story S4.1.1); it is not a Programme Manager action.
    """
    if Role.SEMANTIC_MODEL_ENGINEER not in roles.roles:
        raise ForbiddenError(
            f"generating a model design proposal is the Semantic Model Engineer's; "
            f"declare '{Role.SEMANTIC_MODEL_ENGINEER.value}' in {ROLES_HEADER}"
        )
    return roles


SemanticModelEngineerDep = Annotated[RoleSet, Depends(require_semantic_model_engineer)]


def require_client_data_owner(roles: RoleSetDep) -> RoleSet:
    """Gate an endpoint on the role that owns G2 (spec §13.1, §15.1).

    Approving, requesting changes and (per this codebase's own reading of "I sign off
    what I understand") asking a question the review turns on are the data owner's, for
    their own domain — story S4.2.1.
    """
    if Role.CLIENT_DATA_OWNER not in roles.roles:
        raise ForbiddenError(
            f"reviewing a model design at G2 is the client data owner's; declare "
            f"'{Role.CLIENT_DATA_OWNER.value}' in {ROLES_HEADER}"
        )
    return roles


ClientDataOwnerDep = Annotated[RoleSet, Depends(require_client_data_owner)]


def require_migration_architect(roles: RoleSetDep) -> RoleSet:
    """Gate an endpoint on the role that owns target architecture (spec §2.4: "Owns target
    architecture and conformance rules... Admin"). Editing the conformance ruleset a build
    is checked against is the architect's — story S4.3.2, the first to actually drive this
    role (declared in `roles.py` since S1.1.1, gated nowhere until now)."""
    if Role.MIGRATION_ARCHITECT not in roles.roles:
        raise ForbiddenError(
            f"editing the conformance ruleset is the architect's; declare "
            f"'{Role.MIGRATION_ARCHITECT.value}' in {ROLES_HEADER}"
        )
    return roles


MigrationArchitectDep = Annotated[RoleSet, Depends(require_migration_architect)]


def require_parity_engineer(roles: RoleSetDep) -> RoleSet:
    """Gate an endpoint on the role story S5.1.1 itself names ("As a parity engineer, I
    want every calculated field classified..."). Re-classification changes what every
    CalculatedField node says its own class is, estate-wide — a system pass, not an
    editorial one, but still one somebody triggers, and this is the first route or screen
    to actually drive this role (declared in `roles.py` since S1.1.1, gated nowhere until
    now — the same trajectory `migration_architect` took at S4.3.2)."""
    if Role.PARITY_ENGINEER not in roles.roles:
        raise ForbiddenError(
            f"re-classifying calculated fields is the parity engineer's; declare "
            f"'{Role.PARITY_ENGINEER.value}' in {ROLES_HEADER}"
        )
    return roles


ParityEngineerDep = Annotated[RoleSet, Depends(require_parity_engineer)]


def require_client_analytics_lead(roles: RoleSetDep) -> RoleSet:
    """Gate an endpoint on the role story S7.1.1 itself names as G1's client-side
    approver ("the client analytics lead"). `Role.CLIENT_ANALYTICS_LEAD` is not one of
    §2.4's own eleven roles — see `roles.py`'s own module docstring for the real
    spec-internal gap this closes; this is the first route to ever drive it."""
    if Role.CLIENT_ANALYTICS_LEAD not in roles.roles:
        raise ForbiddenError(
            f"approving the Tolerance Charter at G1 is the client analytics lead's; "
            f"declare '{Role.CLIENT_ANALYTICS_LEAD.value}' in {ROLES_HEADER}"
        )
    return roles


ClientAnalyticsLeadDep = Annotated[RoleSet, Depends(require_client_analytics_lead)]


def require_platform_engineer(roles: RoleSetDep) -> RoleSet:
    """Gate an endpoint on the role story S5.2.1 itself names ("As a platform engineer, I
    want a rules engine..."). Applying rules writes real Measure/MAPS_TO/provenance
    artefacts estate-wide — the first route to drive this role (declared in `roles.py`
    since S1.1.1, gated nowhere until now — the same trajectory `parity_engineer` took at
    S5.1.1). Story S5.5.1 reuses it for the Pattern Library's own MA-11 action (§13.2,
    autonomy ceiling L2, "Platform Engineer approves") — promoting a candidate pattern to
    ACTIVE is the identical persona approving the identical kind of estate-wide,
    deterministic-generation-affecting change."""
    if Role.PLATFORM_ENGINEER not in roles.roles:
        raise ForbiddenError(
            f"applying the deterministic rules engine is the platform engineer's; declare "
            f"'{Role.PLATFORM_ENGINEER.value}' in {ROLES_HEADER}"
        )
    return roles


PlatformEngineerDep = Annotated[RoleSet, Depends(require_platform_engineer)]


def require_migration_engineer(roles: RoleSetDep) -> RoleSet:
    """Gate an endpoint on the role story S5.4.1 itself names ("As a migration engineer, I
    want C4 constructs flagged ... and routed to a redesign decision"). Recording that
    decision is what §3.2's own BLOCKED state clears, on the one real construct this
    platform has for it (`CalculatedField`) — the first route to drive this role (declared
    in `roles.py` since S1.1.1, gated nowhere until now — the same trajectory
    `parity_engineer`/`platform_engineer` each already took)."""
    if Role.MIGRATION_ENGINEER not in roles.roles:
        raise ForbiddenError(
            f"recording a C4 redesign decision is the migration engineer's; declare "
            f"'{Role.MIGRATION_ENGINEER.value}' in {ROLES_HEADER}"
        )
    return roles


MigrationEngineerDep = Annotated[RoleSet, Depends(require_migration_engineer)]


def require_c4_redesign_reader(roles: RoleSetDep) -> RoleSet:
    """Gate C4 redesign visibility on "any Artizent role, or the report owner specifically"
    (story S5.4.1's own "decisions are visible to the report owner"). The report owner is a
    client role with no estate-wide visibility otherwise (`ArtizentDep` would refuse it) —
    this is the first route to ever drive `Role.CLIENT_REPORT_OWNER` (declared since
    S1.1.1, gated nowhere until now), and it is deliberately narrower than opening the
    endpoint to every client role: a different client persona (e.g. the licence admin)
    still has no reason to see a redesign decision."""
    if not (roles.is_artizent() or Role.CLIENT_REPORT_OWNER in roles.roles):
        raise ForbiddenError(
            f"C4 redesign visibility is open to Artizent roles and the report owner; "
            f"declare one in {ROLES_HEADER}"
        )
    return roles


C4RedesignReaderDep = Annotated[RoleSet, Depends(require_c4_redesign_reader)]


def require_tolerance_charter_reader(roles: RoleSetDep) -> RoleSet:
    """Gate Tolerance Charter visibility on "any Artizent role, or the client analytics
    lead specifically" (story S7.1.1) — the identical shape `require_c4_redesign_reader`
    already set for the report owner. The client analytics lead is G1's own client-side
    approver (§13.1); a live gap found by driving the console against this route for
    real: `ArtizentDep` alone would let them approve a charter at G1 they could never
    actually read first. Deliberately narrower than opening the endpoint to every client
    role, the same reasoning `require_c4_redesign_reader` already gives for its own
    reader gate."""
    if not (roles.is_artizent() or Role.CLIENT_ANALYTICS_LEAD in roles.roles):
        raise ForbiddenError(
            f"Tolerance Charter visibility is open to Artizent roles and the client "
            f"analytics lead; declare one in {ROLES_HEADER}"
        )
    return roles


ToleranceCharterReaderDep = Annotated[RoleSet, Depends(require_tolerance_charter_reader)]

DOMAIN_SCOPE_HEADER = "X-Astra-Domain-Scope"


def get_domain_scope(
    domains: Annotated[str | None, Header(alias=DOMAIN_SCOPE_HEADER)] = None,
) -> frozenset[str]:
    """The domain(s) a data owner is asserting authority over — comma-separated, the same
    stated-assertion shape ``roles.py`` already uses for the role header itself (spec
    §18.1: real until E11 maps it from Entra ID group membership; the source of the
    assertion is what is provisional, not whether it is enforced). Absent means none
    asserted — a data owner who names no domain cannot approve a family that has one."""
    if not domains or not domains.strip():
        return frozenset()
    return frozenset(token.strip().lower() for token in domains.split(",") if token.strip())


DomainScopeDep = Annotated[frozenset[str], Depends(get_domain_scope)]


def open_query_log(surface: str, principal: Principal, roles: RoleSet) -> QueryLog:
    return QueryLog(
        surface=surface,
        principal=principal.value,
        roles=str(roles),
        run_id=principal.run_id,
    )
