# Spec §5.3's own three storage/eventing facts, each private-endpoint only (AC: "all
# endpoints private"):
#   "PostgreSQL 16 as the system of record ... Azure Blob for artefacts ...
#    Eventing: CloudEvents over Event Hubs (Kafka protocol)"
# OpenSearch and Temporal have no native azurerm resource -- both are self-hosted on the
# AKS cluster above via their own published Helm charts (deploy/helm/astra-data's own
# Chart.yaml dependencies), which is why this file stops at three services, not five.

data "azurerm_key_vault_secret" "postgres_admin_password" {
  name         = var.postgres_admin_password_secret_name
  key_vault_id = azurerm_key_vault.main.id

  depends_on = [azurerm_role_assignment.keyvault_admin_deployer]
}

resource "azurerm_postgresql_flexible_server" "main" {
  name                = "psql-${local.name_prefix}"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  tags                = var.tags

  version    = "16"
  sku_name   = var.postgres_sku_name
  storage_mb = var.postgres_storage_mb

  administrator_login    = var.postgres_admin_login
  administrator_password = data.azurerm_key_vault_secret.postgres_admin_password.value

  delegated_subnet_id = azurerm_subnet.postgres.id
  private_dns_zone_id = azurerm_private_dns_zone.postgres.id

  zone = "1"

  backup_retention_days        = var.environment == "prod" ? 35 : 7
  geo_redundant_backup_enabled = var.environment == "prod"

  high_availability {
    mode = var.environment == "prod" ? "ZoneRedundant" : "Disabled"
  }

  depends_on = [azurerm_private_dns_zone_virtual_network_link.postgres]

  lifecycle {
    # A rotated admin password changing the plan on every run would be noise; rotate it
    # by updating the Key Vault secret and re-applying with -replace explicitly instead.
    ignore_changes = [administrator_password]
  }
}

resource "azurerm_postgresql_flexible_server_database" "estate" {
  name      = "astra"
  server_id = azurerm_postgresql_flexible_server.main.id
  charset   = "UTF8"
  collation = "en_US.utf8"
}

# ---------------------------------------------------------------------------- Blob

resource "random_string" "storage_suffix" {
  length  = 4
  special = false
  upper   = false
}

resource "azurerm_storage_account" "artefacts" {
  name                = "st${var.project}${var.environment}${random_string.storage_suffix.result}"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  tags                = var.tags

  account_tier                    = "Standard"
  account_replication_type        = var.environment == "prod" ? "ZRS" : "LRS"
  min_tls_version                 = "TLS1_2"
  public_network_access_enabled   = false
  allow_nested_items_to_be_public = false
}

resource "azurerm_storage_container" "artefacts" {
  name                  = "artefacts"
  storage_account_id    = azurerm_storage_account.artefacts.id
  container_access_type = "private"
}

resource "azurerm_private_endpoint" "storage_blob" {
  name                = "pe-${local.name_prefix}-blob"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  subnet_id           = azurerm_subnet.private_endpoints.id
  tags                = var.tags

  private_service_connection {
    name                           = "blob"
    private_connection_resource_id = azurerm_storage_account.artefacts.id
    subresource_names              = ["blob"]
    is_manual_connection           = false
  }

  private_dns_zone_group {
    name                 = "blob"
    private_dns_zone_ids = [azurerm_private_dns_zone.blob.id]
  }
}

# ------------------------------------------------------------------------ Event Hubs

resource "azurerm_eventhub_namespace" "main" {
  name                = "evhns-${local.name_prefix}"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  tags                = var.tags

  sku                           = var.environment == "prod" ? "Standard" : "Basic"
  capacity                      = 1
  public_network_access_enabled = false
}

# One hub for the mutation event stream (events.py's own CloudEvents shape, spec §5.3's
# "consoles are event-sourced views"); a real programme may want one per domain later,
# which is a change to this one resource, not to the module's own shape.
resource "azurerm_eventhub" "estate_events" {
  name              = "estate-events"
  namespace_id      = azurerm_eventhub_namespace.main.id
  partition_count   = 4
  message_retention = var.environment == "prod" ? 7 : 1
}

resource "azurerm_private_endpoint" "eventhub" {
  name                = "pe-${local.name_prefix}-eventhub"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  subnet_id           = azurerm_subnet.private_endpoints.id
  tags                = var.tags

  private_service_connection {
    name                           = "eventhub"
    private_connection_resource_id = azurerm_eventhub_namespace.main.id
    subresource_names              = ["namespace"]
    is_manual_connection           = false
  }

  private_dns_zone_group {
    name                 = "eventhub"
    private_dns_zone_ids = [azurerm_private_dns_zone.eventhub.id]
  }
}
