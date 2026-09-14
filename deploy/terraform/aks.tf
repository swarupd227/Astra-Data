# Spec §5.3: "Runtime: Kubernetes (AKS)." Private cluster (no public API server endpoint,
# the AC's own "all endpoints private"), Entra ID-backed Kubernetes RBAC (the same tenant
# users sign in through for the console, spec §18.1's own "roles map from Entra groups" --
# here at the cluster-admin layer, not the application-role layer entra.py handles),
# and the Key Vault Secrets Store CSI driver addon so `deploy/helm/astra-data`'s own
# SecretProviderClass has a real identity to project secrets with.

resource "azurerm_kubernetes_cluster" "main" {
  name                = "aks-${local.name_prefix}"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  dns_prefix          = "${local.name_prefix}-aks"
  kubernetes_version  = null # let AKS pick its own current stable default
  sku_tier            = var.environment == "prod" ? "Standard" : "Free"
  tags                = var.tags

  private_cluster_enabled = true

  identity {
    type = "SystemAssigned"
  }

  default_node_pool {
    name                         = "system"
    vm_size                      = var.aks_node_vm_size
    node_count                   = var.aks_node_count
    vnet_subnet_id               = azurerm_subnet.aks.id
    os_disk_size_gb              = 128
    only_critical_addons_enabled = false
  }

  network_profile {
    network_plugin    = "azure"
    network_policy    = "azure"              # the NetworkPolicy resources deploy/helm/astra-data ships need something to enforce them
    outbound_type     = "userDefinedRouting" # egress only through azurerm_firewall.main (egress.tf)
    load_balancer_sku = "standard"
    service_cidr      = "10.100.0.0/16"
    dns_service_ip    = "10.100.0.10"
  }

  azure_active_directory_role_based_access_control {
    tenant_id              = var.tenant_id
    azure_rbac_enabled     = true
    admin_group_object_ids = var.admin_group_object_ids
  }

  key_vault_secrets_provider {
    secret_rotation_enabled = true
  }

  depends_on = [azurerm_subnet_route_table_association.aks]
}

resource "azurerm_role_assignment" "aks_network_contributor" {
  # AKS's own cluster identity needs to manage NICs/routes in the subnet it was handed,
  # since this is a "bring your own VNet" deployment rather than AKS creating its own.
  scope                = azurerm_virtual_network.main.id
  role_definition_name = "Network Contributor"
  principal_id         = azurerm_kubernetes_cluster.main.identity[0].principal_id
}
