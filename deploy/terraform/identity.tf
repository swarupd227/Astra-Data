# Spec §18.1: "Users sign in with Entra ID; roles are mapped from Entra groups" -- the
# two app registrations that makes real: graph-svc's own API (the audience a bearer token
# must carry, `ASTRA_ENTRA_CLIENT_ID` in entra.py) and console-web's own single-page-app
# client (`VITE_ENTRA_CLIENT_ID`, lib/entra.ts), which is granted delegated access to the
# API's one real scope.
#
# **Fabric and Tableau service principals are deliberately not created here.** The AC's
# own words are "service principals for Fabric and Tableau live in Key Vault" -- Key
# Vault is this module's (keyvault.tf's); *registering* an app against Power BI/Fabric's
# own tenant settings or Tableau Server/Cloud is the client's own admin action in each of
# those products, not something an Azure Resource Manager deployment can reach into. This
# module creates the empty secret slots (below) the client's own admin fills in; see this
# directory's own README for the exact az/tsh commands to hand them.

resource "random_uuid" "graph_svc_scope_id" {}

resource "azuread_application" "graph_svc_api" {
  display_name     = "Astra Data - graph-svc (${var.environment})"
  identifier_uris  = ["api://astra-graph-svc-${var.environment}"]
  sign_in_audience = "AzureADMyOrg" # this tenant only -- spec §18.1's own "nothing leaves our control"

  api {
    requested_access_token_version = 2

    oauth2_permission_scope {
      id                         = random_uuid.graph_svc_scope_id.result
      admin_consent_description  = "Allow the Astra Data console to call graph-svc as the signed-in user."
      admin_consent_display_name = "Access graph-svc as the signed-in user"
      user_consent_description   = "Allow the Astra Data console to access graph-svc on your behalf."
      user_consent_display_name  = "Access graph-svc"
      enabled                    = true
      type                       = "User"
      value                      = "access_as_user"
    }
  }
}

resource "azuread_service_principal" "graph_svc_api" {
  client_id = azuread_application.graph_svc_api.client_id
}

resource "azuread_application" "console_web" {
  display_name     = "Astra Data - Migration Console (${var.environment})"
  sign_in_audience = "AzureADMyOrg"

  single_page_application {
    redirect_uris = var.console_redirect_uris
  }

  required_resource_access {
    resource_app_id = azuread_application.graph_svc_api.client_id

    resource_access {
      id   = random_uuid.graph_svc_scope_id.result
      type = "Scope"
    }
  }
}

resource "azuread_service_principal" "console_web" {
  client_id = azuread_application.console_web.client_id
}

# Empty on purpose -- see this file's own header comment. `az keyvault secret set
# --vault-name ... --name tableau-rqa --value <token>` (this directory's own README) is
# how a real value actually lands here; Terraform only reserves the name so
# `credentials.py`'s own reference-to-secret-name mapping (`tableau/rqa` -> `tableau-rqa`)
# has somewhere real to resolve against from the first deploy onward.
resource "azurerm_key_vault_secret" "fabric_service_principal_placeholder" {
  name         = "fabric-workspace-sp"
  value        = "REPLACE_ME_VIA_AZ_CLI_NOT_TERRAFORM"
  key_vault_id = azurerm_key_vault.main.id

  lifecycle {
    ignore_changes = [value]
  }

  depends_on = [azurerm_role_assignment.keyvault_admin_deployer]
}

# Story S11.2.1, spec §18.2: "a service principal that has no write on data" for XMLA
# execution -- deliberately a *second*, distinct slot from `fabric-workspace-sp` above,
# not a second reference to the same one. `fabric-workspace-sp` backs `commit`/`deploy`
# (§7.1, a real, already-accepted write path gated to the Steward); this one backs
# `TargetAdapter.evaluate` alone (the DAX/XMLA read this story's own AC is about) and
# must never be granted more than Fabric's own read-only workspace role. Registering the
# app and assigning that role is the client's own Fabric-admin action -- the identical
# "not something an Azure Resource Manager deployment can reach into" limit this file's
# own header comment already states for `fabric-workspace-sp`; this module only reserves
# the name.
resource "azurerm_key_vault_secret" "fabric_execution_service_principal_placeholder" {
  name         = "fabric-execution-sp"
  value        = "REPLACE_ME_VIA_AZ_CLI_NOT_TERRAFORM"
  key_vault_id = azurerm_key_vault.main.id

  lifecycle {
    ignore_changes = [value]
  }

  depends_on = [azurerm_role_assignment.keyvault_admin_deployer]
}

resource "azurerm_key_vault_secret" "tableau_service_principal_placeholder" {
  name         = "tableau-rqa"
  value        = "REPLACE_ME_VIA_AZ_CLI_NOT_TERRAFORM"
  key_vault_id = azurerm_key_vault.main.id

  lifecycle {
    ignore_changes = [value]
  }

  depends_on = [azurerm_role_assignment.keyvault_admin_deployer]
}
