# Terraform — tenant deployment (story S11.1.1)

Provisions the Azure resources spec §5.3/§18.1 name for an in-tenant deployment: AKS,
Azure Database for PostgreSQL (Flexible Server), Blob storage, Event Hubs, Key Vault,
networking with an egress allow-list, and the two Entra ID app registrations (graph-svc's
own API, console-web's own SPA client) that make §18.1's "users sign in with Entra ID"
real. OpenSearch and Temporal are not here — see the repo root README and
`deploy/helm/astra-data` for why (they deploy onto the AKS this module creates, as
Kubernetes workloads, not Azure resources).

**Scope for this story: local validation only.** This module is real, complete Terraform,
not a sketch — but it has never been applied against a live subscription from this
session. `terraform fmt -check`, `terraform init` and `terraform validate` all run clean
here (no credentials required for any of the three); `terraform plan` needs a real,
authenticated Azure session and is therefore the client's own to run, from their own
Cloud Shell against their own subscription — see "Running this for real" below.

## Running this for real

From the client's Azure Cloud Shell (subscription: whichever one the programme's own
Entra tenant admin names — this project's own sandbox subscription during development is
"Microsoft Azure Sponsorship"):

```bash
az login
az account set --subscription "<subscription name or id>"

cd deploy/terraform
cp terraform.tfvars.example terraform.tfvars
# edit terraform.tfvars: subscription_id, tenant_id, admin_group_object_ids, console_redirect_uris

terraform init
terraform plan -out=tfplan
terraform apply tfplan
```

**The PostgreSQL admin password needs a second pass**, because `variables.tf`'s own
`postgres_admin_password_secret_name` is read from Key Vault, not typed into a
`.tfvars` file (see that variable's own description for why):

```bash
# 1. create everything except PostgreSQL, so the vault exists to seed a secret into
terraform apply -target=azurerm_key_vault.main -target=azurerm_role_assignment.keyvault_admin_deployer tfplan

# 2. seed the password (generate one, do not choose something memorable)
az keyvault secret set \
  --vault-name "$(terraform output -raw key_vault_name)" \
  --name postgres-admin-password \
  --value "$(openssl rand -base64 32)"

# 3. everything else, PostgreSQL included
terraform apply tfplan
```

**Fabric and Tableau service principals** (`identity.tf`'s own header comment explains
why Terraform cannot create these itself): register them in Power BI/Fabric admin and
Tableau Server/Cloud's own admin console, then:

```bash
az keyvault secret set --vault-name "$(terraform output -raw key_vault_name)" \
  --name tableau-rqa --value "<the real Tableau personal access token or connected-app secret>"
az keyvault secret set --vault-name "$(terraform output -raw key_vault_name)" \
  --name fabric-workspace-sp --value "<the real Fabric service principal secret>"
```

**After apply**, wire the outputs into graph-svc/console-web's own configuration —
`outputs.tf` names exactly which environment variable each one is:

```bash
terraform output astra_entra_tenant_id      # -> ASTRA_ENTRA_TENANT_ID
terraform output astra_entra_client_id      # -> ASTRA_ENTRA_CLIENT_ID
terraform output astra_key_vault_url        # -> ASTRA_KEY_VAULT_URL
terraform output vite_entra_tenant_id       # -> VITE_ENTRA_TENANT_ID
terraform output vite_entra_client_id       # -> VITE_ENTRA_CLIENT_ID
terraform output vite_entra_api_scope       # -> VITE_ENTRA_API_SCOPE
```

Also set `ASTRA_ENTRA_GROUP_ROLE_MAP` by hand (`entra.py`'s own `parse_group_role_map`) —
no Terraform resource here decides which of the client's Entra groups means
`programme_manager` versus `client_report_owner`; that mapping is the client's own call.

Then deploy the workloads themselves with `deploy/helm/astra-data` (its own README
documents `az aks get-credentials`, private-cluster network access, and `helm upgrade
--install`), and finally record the deployment's own bill of materials with
`services/graph-svc/tools/generate_bom.py submit` (`bom.py`'s own docstring).

## What "local validate only" actually checked

```bash
terraform fmt -check
terraform init -backend=false   # no remote backend configured yet -- see versions.tf
terraform validate
```

`terraform plan`/`apply` were **not** run from this session — they need a real Azure
login this environment does not have, and provisioning real cloud resources is outside
this story's own agreed scope (see the ADR).
