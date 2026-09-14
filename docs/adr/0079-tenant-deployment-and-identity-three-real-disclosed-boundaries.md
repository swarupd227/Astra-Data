# ADR 0079 — Tenant deployment and identity: three real, disclosed boundaries

Status: accepted · 14 September 2026 · Story S11.1.1, opening F11.1 (E11)

## Context

S11.1.1 — the backlog's own AC, verbatim: *"As an InfoSec reviewer, I want the platform
deployed into our Azure subscription with our identity provider, so that nothing about
our estate leaves our control."*

- Helm chart and Terraform module deploy AKS services and workers, Azure Database for
  PostgreSQL, Blob, Event Hubs, Key Vault, OpenSearch, Temporal; all endpoints private;
  egress limited to an allow-list
- Users sign in with Entra ID; roles are mapped from Entra groups; service principals for
  Fabric and Tableau live in Key Vault
- A deployment produces a signed bill of materials (images, versions, chart values)
  stored as an evidence record

This is the first story in this codebase whose own AC is infrastructure-as-code and
identity-provider integration rather than application-layer feature work, and the first
where I was genuinely blocked on three decisions only the user could make. All three were
put to the user directly (`AskUserQuestion`) before any code was written, and answered:

1. **Azure deployment scope** → *"Local validate only."* Write real Helm/Terraform,
   validate locally (`terraform fmt`/`validate`, `helm lint`/`template`), hand the user
   the `az`/`terraform`/`helm` commands to run themselves from their own Cloud Shell — the
   same "hand over az commands for a fresh Cloud Shell" posture already established for
   this project's own Azure work. **No live Azure resource was provisioned by this
   session.**
2. **Entra ID integration** → *"Build it disclosed, not yet connected."* Real,
   working OIDC/JWT validation and group→role mapping, with real tests, honestly
   disclosed as never run against a live Entra tenant — the identical posture
   `directory.py`'s own `NullDirectoryResolver` and `credentials.py`'s own
   `EnvironmentCredentialProvider` already carry for their own E11-shaped gaps.
3. **Story sequencing** → *"All three AC bullets, one story, one commit"* — the same
   shape every prior story in this programme has shipped in.

## Decisions

### 1. Three separate identity/credential surfaces, not one

`principal.py`'s own docstring named the plan years before this story existed: "the
header is replaced by a verified identity in E11 without the ontology or the write path
changing shape." This story keeps that promise literally, in three pieces that each
extend an existing, real interface rather than replacing it:

- **`entra.py`** (new) — JWKS fetch/cache, RS256 verification, issuer/audience/expiry
  checks, `parse_group_role_map`. Wired into `api/deps.py`'s `get_bearer_claims`, which
  `get_principal`/`get_role_set` now check *first*: a verified `Authorization: Bearer`
  token wins when Entra is configured and one is presented; every other request —
  every request in every environment this project has today — falls through to the
  existing `X-Astra-Principal`/`X-Astra-Roles` headers exactly as before. Unconfigured
  (`ASTRA_ENTRA_TENANT_ID`/`ASTRA_ENTRA_CLIENT_ID` unset) is the honest default: `get_
  entra_config()` returns `None` before the Authorization header is even inspected.
- **`credentials.KeyVaultCredentialProvider`** (new) — implements the same `Credential
  Provider` protocol `EnvironmentCredentialProvider` already does; selected by `harvest_
  setup.build_credential_provider` only when `ASTRA_KEY_VAULT_URL` is set. Uses the
  *async* Azure SDK clients (`azure.identity.aio`, `azure.keyvault.secrets.aio`), not the
  sync ones — `resolve()` runs on graph-svc's own event loop, and a harvest can resolve
  several site credentials concurrently; a blocking SDK call here would stall every other
  request the process is serving. A reference's `/` becomes Key Vault's own `-`
  (`tableau/rqa` → `tableau-rqa`), since a vault secret name cannot hold a slash.
- **`bom.py`** (new) — a real Ed25519-signed deployment bill of materials. See decision 3
  for why this one is cryptographic rather than this codebase's usual textual "signed"
  attestation.

Each is disclosed, in its own module docstring, as real-but-unverified-against-a-live-
tenant — never silently claimed as complete. `entra.py`'s JWKS cache and JWT validation
are tested against a locally generated RSA keypair standing in for Entra's own signing
key; `KeyVaultCredentialProvider` is tested against a fake async secret client standing
in for a live vault.

### 2. One console-side bearer token, layered onto the existing role picker — not a replacement

`App.tsx`'s own "Acting as" selector predates this story and remains the only mechanism
that decides console-side nav (`visibleSurfacesFor`) — real sign-in through `lib/
entra.ts` (wrapping `@azure/msal-browser`) adds a bearer token to `Identity.bearerToken`,
sent as `Authorization: Bearer` *alongside* the existing `X-Astra-Principal`/`X-Astra-
Roles` headers, never instead of them. graph-svc prefers the verified token when both are
present, so a signed-in viewer's console-side nav — the role picker's own choice — can in
principle disagree with what a real Entra-mapped role can actually reach, until the
picker is retired for a real "who am I" call once every client role has a live tenant
to map against. This is the identical, already-disclosed "nav-level convenience only;
every mutating call is still re-checked server-side" limitation the role picker's own
`App.tsx` docstring already states for every existing role — this story does not
introduce a new class of gap, it extends the existing one to a new identity source.

**A static SPA cannot read a Kubernetes environment variable at runtime.** Vite bakes
every `VITE_*` value into the bundle at `npm run build` (confirmed: `services/console-
web/Dockerfile`'s own two-stage build compiles once, then ships static files behind
nginx with no server-side process to inject configuration later). `VITE_ENTRA_CLIENT_ID`/
`VITE_ENTRA_TENANT_ID`/`VITE_ENTRA_API_SCOPE` are therefore new Docker build `ARG`s
(mirroring the existing `VITE_ASTRA_ENV` pattern exactly), not Helm chart values — the
Helm chart's own `values.yaml` says so explicitly at `consoleWeb.image`, and its README
gives the real `docker build --build-arg` command. Getting this wrong (treating them as
runtime env vars) would have shipped a console that silently never signs in.

### 3. A real Ed25519 signature for the bill of materials — a deliberate departure from this codebase's own convention

`decision_register.py`'s PDF and `calibration_wave.py`'s report are each "signed" by
rendering the approver's name, role and timestamp into the document — adequate for a
human-readable approval record nobody disputes the origin of, because nothing in those
stories' own AC calls the property "tamper-evident." E11's own goal, stated in the epic
preamble this story opens, is different: *"every event is in a tamper-evident chain."* A
rendered PDF footer is not tamper-evident — anyone with write access to the artefact
store can edit it and it still reads as signed. `bom.py` uses a real Ed25519 signature
(`cryptography`) instead: `sign_document`/`verify_signature` operate over the document's
canonical JSON bytes (reusing `context/canonical.py`'s own `canonical_json`/`context_
hash`, the identical canonicalisation `ContextAssembler` already established), and a
single flipped byte anywhere in the document fails verification. This is a one-off
departure, not a new house style — the textual convention stays correct for every gate
decision and report this codebase already produces; this is the one artefact whose own
story is explicitly about the difference between "signed" and "tamper-evident."

**The private key never reaches graph-svc**, the identical "a secret never crosses the
API" discipline `credentials.py` already established for source credentials.
`tools/generate_bom.py` (new CLI) builds, signs and submits the bill of materials from
wherever the deployment pipeline actually runs; `POST /v1/deployment/bom` accepts an
already-signed envelope and never asks for a key. `GET /v1/deployment/bom/{id}` recomputes
`signature_verified` at *read* time against `Settings.bom_public_key_pem` (never stored),
so a key published after a BOM was recorded, or rotated, is still checked correctly —
`null` (not `false`) when no public key is configured yet, distinguishing "not yet
checkable" from "checked and failed." Chart *values* are represented by a hash, not
embedded verbatim: a values file can carry a tenant's own hostnames or sizing, and this
record is evidence that a specific configuration was deployed, not a second copy of that
configuration to keep in sync.

### 4. Terraform: real resources, `local validate only`, every honest gap named where it lives

`deploy/terraform` provisions everything the AC names with a native Azure resource — AKS
(private cluster, Entra-backed Kubernetes RBAC), Azure Database for PostgreSQL Flexible
Server (VNet-delegated, no public endpoint), Storage (Blob, private endpoint), Event Hubs
(private endpoint), Key Vault (RBAC-authorized, private endpoint), an Azure Firewall
implementing the egress allow-list via the AKS subnet's own user-defined route, and the
two Entra ID app registrations (`graph-svc`'s own API, `console-web`'s own SPA client)
that make bullet two real. **OpenSearch and Temporal have no native `azurerm` resource**
(confirmed: neither is an Azure PaaS offering) — both are self-hosted on the AKS this
module creates via `deploy/helm/astra-data`'s own chart dependencies on their real,
published upstream charts (`opensearch/opensearch`, `temporal/temporal`), which is the
direct reason this story needed both a Terraform module *and* a Helm chart as two
separate, real artifacts rather than one.

**Fabric and Tableau service principals are not created by this module** — registering
an app against Power BI/Fabric's own tenant settings or Tableau Server/Cloud is the
client's own admin action in each of those products, not something an Azure Resource
Manager deployment can reach into. `identity.tf` creates the empty Key Vault secret slots
(`tableau-rqa`, `fabric-workspace-sp`) `credentials.py`'s own reference-to-secret-name
mapping already expects, `ignore_changes = [value]` so a real value set later via `az
keyvault secret set` is never reverted by a subsequent `apply` — the module's own README
gives the exact command.

**The PostgreSQL administrator password is a two-phase `apply`, not a `.tfvars` value.**
`postgres_admin_password_secret_name` names a Key Vault secret a `data` source reads back
— `credentials.py`'s "a secret never crosses request-config plumbing" discipline applied
to Terraform's own inputs, not only graph-svc's. The module's README documents the real
sequence: apply the Key Vault first, seed the secret via `az keyvault secret set`, apply
everything else.

**"Local validate only" was checked, literally, not asserted**: `terraform fmt -check`,
`terraform init -backend=false` and `terraform validate` all ran clean from this session
(no credentials required for any of the three — confirmed by direct run, not assumption).
`terraform plan` was attempted and failed exactly where expected — `could not parse Azure
CLI version: launching Azure CLI: exec: "az": executable file not found` — after
Terraform had already computed the plan for every resource with no live-Azure dependency
(the two `random_string`/`random_uuid` resources), proving the failure is "no Azure
session here," not a defect in the configuration. `terraform plan`/`apply` against a real
subscription are the client's own, from their own Cloud Shell — the module's README gives
the exact commands.

### 5. Helm: NetworkPolicy and the firewall are two layers, not one

`deploy/helm/astra-data`'s NetworkPolicies default-deny every pod in the release and
allow exactly the ports each real workload uses (graph-svc↔console-web, DNS, HTTPS,
PostgreSQL, Event Hubs' Kafka-protocol port, and OpenSearch/Temporal's own ports when
enabled) — but a `NetworkPolicy` cannot see a hostname, only an IP and a port, so it
cannot implement the AC's own "egress limited to an allow-list" by itself. That is
`deploy/terraform`'s own Azure Firewall's job (decision 4): the AKS subnet's user-defined
route forces every outbound packet through it, and its one application rule collection is
the real FQDN allow-list (`api.anthropic.com`, `login.microsoftonline.com`, Key
Vault/Blob/ACR). Two independent, disclosed layers — the NetworkPolicy says which ports a
pod may use at all, the firewall says which hostnames an HTTPS connection on 443 may
actually reach — deliberately not collapsed into one mechanism.

**Secrets are projected from Key Vault via the CSI driver's `SecretProviderClass`**
(`templates/secretproviderclass.yaml`), never written into a Helm value or a hand-created
Kubernetes Secret. `secretObjects` additionally syncs the projected value into a real
Kubernetes `Secret` graph-svc's own `Deployment` references via `secretKeyRef` — the
shape a FastAPI process reading `ASTRA_POSTGRES_PASSWORD` from its environment actually
needs, since `config.py`'s own `_env()` cannot read a mounted file.

**"Local validate only" here too, and checked the same way**: `helm dependency update`
(fetching the real `opensearch`/`temporal` chart archives), `helm lint` and `helm
template` all ran clean, the last with every optional feature (`graphSvc.keyVault.
enabled`, `opensearch.enabled`, `temporal.enabled`) turned on together so every
conditional branch in every template actually rendered at least once, not only the
disabled-by-default path. `helm install` against a live cluster is, again, the client's
own to run.

### 6. A real `/healthz` route, added because the Helm chart's own probes needed one

Building `deploy/helm/astra-data`'s liveness/readiness probes surfaced a genuine,
pre-existing gap: graph-svc has never had a health endpoint (confirmed: no route, no
Dockerfile `HEALTHCHECK`, no `docker-compose.yml` healthcheck exist anywhere in this
codebase before this story — only `console-web`'s own nginx `/healthz` did). Shipping a
`Deployment` whose own probes point at a route that does not exist would be a chart that
looks complete and silently never becomes Ready. `main.py` gained one real, minimal
`GET /healthz` — process-only, no database dependency, the identical "cheap, no
dependency check" shape `console-web`'s own nginx `/healthz` already has, so a transient
Postgres blip surfaces through every other route's own 5xx rather than taking every
otherwise-healthy replica out of rotation with it. `Dockerfile` gained a matching
`HEALTHCHECK` using Python's own stdlib `urllib` (not `curl`/`wget` — neither is
installed, and the image's whole point is carrying no shell tooling it does not need).

## Consequences

- `services/graph-svc`: new `entra.py`, `bom.py`; `credentials.py` gained
  `KeyVaultCredentialProvider`; `harvest_setup.build_credential_provider` selects it when
  `ASTRA_KEY_VAULT_URL` is set; `config.py` gained `entra_tenant_id`/`entra_client_id`/
  `entra_group_role_map`/`key_vault_url`/`bom_public_key_pem`; `api/deps.py`'s `get_
  principal`/`get_role_set` gained an optional, backward-compatible `get_bearer_claims`
  dependency (`api/graphql/router.py`'s own manual call site updated to match); new
  `api/routes_deployment_bom.py` (`POST`/`GET /v1/deployment/bom`, `GET .../{id}`), new
  `require_deployment_bom_reader` dep (Artizent or the InfoSec reviewer — the identical
  shape `require_decision_register_reader` already set for the same client role); new
  `GET /healthz`; new `tools/generate_bom.py`. New real dependencies: `pyjwt[crypto]`,
  `cryptography`, `azure-identity`, `azure-keyvault-secrets`, `aiohttp` (the async
  credential chain's own transport), `PyYAML` (`generate_bom.py`'s own values-file
  reading); `httpx` promoted from dev-only to a real runtime dependency (JWKS fetch).
- `services/console-web`: new `lib/entra.ts` (wraps `@azure/msal-browser`); `lib/api.ts`'s
  `Identity` gained `bearerToken`, sent as `Authorization: Bearer` alongside the existing
  header pair; `App.tsx` renders "Sign in with Microsoft" only when `VITE_ENTRA_CLIENT_
  ID`/`VITE_ENTRA_TENANT_ID` are configured (unset in every environment this project has
  today, so the existing role picker remains the only visible identity affordance);
  `Dockerfile` gained the three matching build `ARG`s; new `vite-env.d.ts` declarations.
- `deploy/terraform` (new): `versions.tf`, `providers.tf`, `variables.tf`, `network.tf`,
  `egress.tf`, `aks.tf`, `keyvault.tf`, `data_services.tf`, `identity.tf`, `outputs.tf`,
  `terraform.tfvars.example`, `README.md`.
- `deploy/helm/astra-data` (new): `Chart.yaml` (opensearch/temporal as real, disabled-
  by-default dependencies), `values.yaml`, `templates/` (graph-svc and console-web
  Deployment+Service, `SecretProviderClass`, `NetworkPolicy`, `NOTES.txt`, `_helpers.tpl`),
  `README.md`.
- `.github/workflows/ci.yml`: new `deploy` job — `terraform fmt`/`init -backend=false`/
  `validate`, `helm dependency update`/`lint`/`template` (every optional feature on),
  the identical "checked for real, in CI, every time" bar every other job already sets.
- Verified: `services/graph-svc` — real unit tests for `entra.py` (JWT validation against
  a locally generated RSA keypair: valid, wrong key, expired, wrong audience, wrong
  issuer, unknown `kid`, malformed token, malformed identity claim; JWKS cache TTL
  behaviour), `bom.py` (sign/verify round-trip, tamper detection on every field, wrong
  key, malformed signature/key handled as `False` not an exception), `credentials.py`'s
  `KeyVaultCredentialProvider` (against a fake async secret client: resolve, missing
  secret, empty value, reference validation), `api/deps.py`'s Entra wiring (bearer-token-
  first precedence, fallback when unconfigured/absent/non-bearer, 401 on an invalid
  token when configured, an HTTP-level round trip with no `X-Astra-*` header at all), the
  new `/healthz` route and `POST`/`GET /v1/deployment/bom` routes (role gating, schema
  validation, `signature_verified: null` with no key configured); the full unit suite
  re-run clean afterward (1504 passed, unchanged, confirming the Entra/Key-Vault wiring
  broke nothing already there); `ruff`/`mypy` clean across every new and changed file.
  `services/console-web` — real tests for `lib/entra.ts` (config gating, cache-first
  lookup before an interactive popup, sign-in/out, silent-then-popup token acquisition)
  against a mocked `@azure/msal-browser`, and for `App.tsx`'s own sign-in affordance
  (hidden when unconfigured, a real sign-in/out round trip, the acquired token reaching
  `Identity.bearerToken`) against a mocked `lib/entra`; the full suite re-run clean
  (467 passed, up from 446); `tsc --noEmit`/`eslint` clean. `deploy/terraform` —
  `fmt -check`/`init -backend=false`/`validate` all clean; `plan` attempted and failed
  only on missing Azure CLI auth (see decision 4). `deploy/helm/astra-data` — `helm
  dependency update`/`lint`/`template` (every optional feature enabled) all clean.

## Alternatives considered

**Provision real Azure resources from this session**, to prove the module end-to-end.
Rejected by the user's own explicit answer before any code was written — this session has
no Azure credentials, and provisioning real cloud infrastructure without the client's own
sign-off is exactly the kind of action this codebase's own safety posture exists to
avoid. "Local validate only" is not a lesser bar met with less effort; it is the real,
agreed bar this story was scoped to.

**Wait for a live Entra tenant before writing any identity code.** Rejected — the same
reasoning `directory.py`'s own `NullDirectoryResolver` and `credentials.py`'s own
`EnvironmentCredentialProvider` already establish: a real, tested, disclosed interface
today is worth more than a placeholder, and the moment a tenant exists, `entra.py`'s own
`validate_token` needs no code change — only configuration.

**A single Terraform module covering everything, including OpenSearch/Temporal as
`azurerm_container_group`s or hand-rolled Kubernetes manifests.** Rejected — see decision
4. Neither service has a native Azure resource, and both have real, actively maintained
upstream Helm charts; reimplementing what those charts already do correctly would be
strictly worse than depending on them, and would leave this deployment permanently behind
upstream security fixes.

**Textual "signed" attestation for the bill of materials, matching every other "signed"
artefact in this codebase.** Rejected — see decision 3. This is the one story whose own
epic explicitly promises a tamper-evident chain; using the existing non-cryptographic
convention here would quietly undercut the epic's own stated goal on its very first
story.

**Treat `VITE_ENTRA_*` as ordinary Helm-configurable runtime environment variables**,
matching how `graphSvc.env` works. Rejected — see decision 2: a static SPA served by
nginx has no process to read a Kubernetes env var at request time. Doing this would have
shipped a console whose sign-in silently never activates no matter what the Helm values
said.
