# The AC's own "egress limited to an allow-list", and spec §5.3's "default-deny egress;
# only broker-approved endpoints are reachable" -- an Azure Firewall the AKS subnet's own
# route table (network.tf) forces every outbound packet through, with exactly one
# application rule collection: the FQDNs in var.egress_allow_list_fqdns, on 443 only.
# Everything else is the firewall's own implicit deny.

resource "azurerm_public_ip" "firewall" {
  name                = "pip-${local.name_prefix}-firewall"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  allocation_method   = "Static"
  sku                 = "Standard"
  tags                = var.tags
}

resource "azurerm_firewall" "main" {
  name                = "fw-${local.name_prefix}"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  sku_name            = "AZFW_VNet"
  sku_tier            = "Standard"
  tags                = var.tags

  ip_configuration {
    name                 = "firewall-ipconfig"
    subnet_id            = azurerm_subnet.firewall.id
    public_ip_address_id = azurerm_public_ip.firewall.id
  }
}

resource "azurerm_firewall_application_rule_collection" "allow_list" {
  name                = "allow-list"
  azure_firewall_name = azurerm_firewall.main.name
  resource_group_name = azurerm_resource_group.main.name
  priority            = 100
  action              = "Allow"

  rule {
    name             = "broker-approved-endpoints"
    source_addresses = [azurerm_subnet.aks.address_prefixes[0]]
    target_fqdns     = var.egress_allow_list_fqdns

    protocol {
      port = 443
      type = "Https"
    }
  }
}

# DNS resolution itself has to reach somewhere before a name can even be checked against
# the FQDN allow-list above -- Azure's own recursive resolver, not the open internet.
resource "azurerm_firewall_network_rule_collection" "dns" {
  name                = "dns"
  azure_firewall_name = azurerm_firewall.main.name
  resource_group_name = azurerm_resource_group.main.name
  priority            = 100
  action              = "Allow"

  rule {
    name                  = "azure-dns"
    source_addresses      = [azurerm_subnet.aks.address_prefixes[0]]
    destination_addresses = ["168.63.129.16"]
    destination_ports     = ["53"]
    protocols             = ["UDP", "TCP"]
  }
}
