output "resource_group_name" {
  value = azurerm_resource_group.main.name
}

output "aks_cluster_name" {
  value = azurerm_kubernetes_cluster.main.name
}

output "aks_get_credentials_command" {
  description = "Hand this to whoever needs kubectl access -- private cluster, so it must run from within the VNet or over the AKS run-command/Azure Bastion path (this module does not provision either; see the README)."
  value       = "az aks get-credentials --resource-group ${azurerm_resource_group.main.name} --name ${azurerm_kubernetes_cluster.main.name} --overwrite-existing"
}

output "key_vault_name" {
  value = azurerm_key_vault.main.name
}

output "key_vault_uri" {
  value = azurerm_key_vault.main.vault_uri
}

output "postgres_server_fqdn" {
  value = azurerm_postgresql_flexible_server.main.fqdn
}

output "storage_account_name" {
  value = azurerm_storage_account.artefacts.name
}

output "eventhub_namespace_name" {
  value = azurerm_eventhub_namespace.main.name
}

# ---------------------------------------------------------- graph-svc's own Settings

output "astra_entra_tenant_id" {
  description = "ASTRA_ENTRA_TENANT_ID on graph-svc."
  value       = var.tenant_id
}

output "astra_entra_client_id" {
  description = "ASTRA_ENTRA_CLIENT_ID on graph-svc -- the API app registration's own client id."
  value       = azuread_application.graph_svc_api.client_id
}

output "astra_key_vault_url" {
  description = "ASTRA_KEY_VAULT_URL on graph-svc."
  value       = azurerm_key_vault.main.vault_uri
}

# ------------------------------------------------------------- console-web's own env

output "vite_entra_tenant_id" {
  value = var.tenant_id
}

output "vite_entra_client_id" {
  value = azuread_application.console_web.client_id
}

output "vite_entra_api_scope" {
  value = "api://astra-graph-svc-${var.environment}/access_as_user"
}
