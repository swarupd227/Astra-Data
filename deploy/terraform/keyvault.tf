# Spec §18.1: "service principals for Fabric and Tableau live in Key Vault"; credentials.py's
# own KeyVaultCredentialProvider (story S11.1.1) resolves `tableau/rqa` here as the secret
# `tableau-rqa`. Private endpoint only -- no public network access, per the AC's own "all
# endpoints private" -- and RBAC authorization rather than the older access-policy model,
# so the same `admin_group_object_ids` this module already gates AKS admin behind (aks.tf)
# also gates who can read a secret directly, not a second permission model to keep in sync.

resource "random_string" "keyvault_suffix" {
  length  = 4
  special = false
  upper   = false
}

resource "azurerm_key_vault" "main" {
  name                = "kv-${var.project}${var.environment}${random_string.keyvault_suffix.result}"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  tenant_id           = var.tenant_id
  sku_name            = "standard"
  tags                = var.tags

  rbac_authorization_enabled    = true
  purge_protection_enabled      = true
  soft_delete_retention_days    = 90
  public_network_access_enabled = false

  network_acls {
    default_action = "Deny"
    bypass         = "AzureServices"
  }
}

resource "azurerm_private_endpoint" "keyvault" {
  name                = "pe-${local.name_prefix}-keyvault"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  subnet_id           = azurerm_subnet.private_endpoints.id
  tags                = var.tags

  private_service_connection {
    name                           = "keyvault"
    private_connection_resource_id = azurerm_key_vault.main.id
    subresource_names              = ["vault"]
    is_manual_connection           = false
  }

  private_dns_zone_group {
    name                 = "keyvault"
    private_dns_zone_ids = [azurerm_private_dns_zone.vault.id]
  }
}

data "azurerm_client_config" "current" {}

# Whoever runs `terraform apply` needs to seed the PostgreSQL admin password secret
# (variables.tf's own two-phase `apply` note) before data_services.tf's data source can
# read it back.
resource "azurerm_role_assignment" "keyvault_admin_deployer" {
  scope                = azurerm_key_vault.main.id
  role_definition_name = "Key Vault Administrator"
  principal_id         = data.azurerm_client_config.current.object_id
}

resource "azurerm_role_assignment" "keyvault_admin_groups" {
  for_each             = toset(var.admin_group_object_ids)
  scope                = azurerm_key_vault.main.id
  role_definition_name = "Key Vault Administrator"
  principal_id         = each.value
}

# AKS's own kubelet identity (aks.tf) reads secrets through the Key Vault CSI driver's
# SecretProviderClass (deploy/helm/astra-data's own templates/secretproviderclass.yaml) --
# read-only, deliberately narrower than the admin role above.
resource "azurerm_role_assignment" "keyvault_secrets_user_aks" {
  scope                = azurerm_key_vault.main.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_kubernetes_cluster.main.key_vault_secrets_provider[0].secret_identity[0].object_id
}
