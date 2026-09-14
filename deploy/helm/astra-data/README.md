# Helm — astra-data (story S11.1.1)

Deploys graph-svc and console-web onto the AKS cluster `deploy/terraform` provisions, plus
OpenSearch and Temporal (spec §5.3) as real upstream Helm chart dependencies — both
disabled by default (`opensearch.enabled` / `temporal.enabled`), since most environments
never touch search and a fresh dev/test stack doesn't need durable workflow state on day
one. NetworkPolicies (`templates/networkpolicy.yaml`) default-deny every pod in the
release and allow exactly the ports this chart's own services actually use; a
`SecretProviderClass` (`templates/secretproviderclass.yaml`) projects the Key Vault
`deploy/terraform` created, so no plain-text secret is ever written into a `values.yaml`
or a `kubectl apply`.

**Scope for this story: local validation only** — `helm lint` and `helm template` both
run clean against this chart (see "What was actually checked" below); `helm install`
has not been run against a live cluster from this session, matching the identical scope
`deploy/terraform`'s own README discloses for `terraform apply`.

## Running this for real

After `deploy/terraform apply` has produced a real AKS cluster and its own outputs:

```bash
az aks get-credentials --resource-group <terraform output: resource_group_name> \
  --name <terraform output: aks_cluster_name> --overwrite-existing
# A private cluster (terraform's own aks.tf) has no public API server endpoint -- this
# needs to run from inside the VNet (an Azure Bastion host, a jumpbox, or `az aks command
# invoke`), not from an arbitrary laptop with `az` installed.

helm dependency update deploy/helm/astra-data

helm upgrade --install astra-data deploy/helm/astra-data \
  --namespace astra-data --create-namespace \
  --set graphSvc.image.tag=<the real built image tag> \
  --set consoleWeb.image.tag=<the real built image tag> \
  --set graphSvc.env.ASTRA_POSTGRES_HOST=$(terraform -chdir=deploy/terraform output -raw postgres_server_fqdn) \
  --set graphSvc.env.ASTRA_ENTRA_TENANT_ID=$(terraform -chdir=deploy/terraform output -raw astra_entra_tenant_id) \
  --set graphSvc.env.ASTRA_ENTRA_CLIENT_ID=$(terraform -chdir=deploy/terraform output -raw astra_entra_client_id) \
  --set graphSvc.env.ASTRA_KEY_VAULT_URL=$(terraform -chdir=deploy/terraform output -raw astra_key_vault_url) \
  --set graphSvc.keyVault.enabled=true \
  --set graphSvc.keyVault.name=$(terraform -chdir=deploy/terraform output -raw key_vault_name) \
  --set graphSvc.keyVault.tenantId=$(terraform -chdir=deploy/terraform output -raw astra_entra_tenant_id)
  # graphSvc.keyVault.userAssignedIdentityClientId: the AKS Key Vault Secrets Provider
  # addon's own managed identity -- `az aks show --query addonProfiles.azureKeyvaultSecretsProvider.identity.clientId`
```

**`console-web`'s own Entra ID configuration is baked into its image at build time**, not
set here — a static SPA bundle has no way to read a Kubernetes env var (see the chart's
own `values.yaml`, `consoleWeb.image`'s comment, and `services/console-web/Dockerfile`).
Build it with:

```bash
docker build -f services/console-web/Dockerfile \
  --build-arg VITE_ASTRA_ENV=prod \
  --build-arg VITE_ENTRA_CLIENT_ID=$(terraform -chdir=deploy/terraform output -raw vite_entra_client_id) \
  --build-arg VITE_ENTRA_TENANT_ID=$(terraform -chdir=deploy/terraform output -raw vite_entra_tenant_id) \
  --build-arg VITE_ENTRA_API_SCOPE=$(terraform -chdir=deploy/terraform output -raw vite_entra_api_scope) \
  -t <registry>/astra/console-web:<tag> .
```

Then record the deployment's own signed bill of materials — `helm template` NOTES.txt
(shown after `helm upgrade --install`) has the exact command.

## What was actually checked

```bash
helm dependency update deploy/helm/astra-data
helm lint deploy/helm/astra-data
helm template astra-data deploy/helm/astra-data \
  --set graphSvc.keyVault.enabled=true --set opensearch.enabled=true --set temporal.enabled=true
```

All three ran clean against every real template in this chart, with every optional
feature (Key Vault projection, OpenSearch, Temporal) turned on so every conditional
branch actually rendered at least once — not just the disabled-by-default path.
