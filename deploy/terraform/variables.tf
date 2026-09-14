variable "subscription_id" {
  description = "The client's Azure subscription id (spec §18.1: the platform runs inside the client's tenant)."
  type        = string
}

variable "tenant_id" {
  description = "The client's Entra ID tenant id -- the same tenant users sign in through (ASTRA_ENTRA_TENANT_ID on graph-svc)."
  type        = string
}

variable "project" {
  description = "Short name prefixed onto every resource name."
  type        = string
  default     = "astra"

  validation {
    condition     = can(regex("^[a-z][a-z0-9]{1,10}$", var.project))
    error_message = "project must be lowercase alphanumeric, 2-11 characters (it prefixes Storage Account and Key Vault names, which have their own tight length/character limits)."
  }
}

variable "environment" {
  description = "prod, test or dev -- mirrors ASTRA_ENV / the console's own environment chip (§15.6)."
  type        = string

  validation {
    condition     = contains(["prod", "test", "dev"], var.environment)
    error_message = "environment must be one of: prod, test, dev."
  }
}

variable "location" {
  description = "Azure region. Defaults to a real, commonly available region; the client's own residency requirements decide the real value."
  type        = string
  default     = "uksouth"
}

variable "admin_group_object_ids" {
  description = "Entra ID group object ids granted AKS cluster-admin (azure_active_directory_role_based_access_control) and Key Vault administrator RBAC. At least one is required -- an AKS private cluster with no admin group configured has no break-glass path."
  type        = list(string)

  validation {
    condition     = length(var.admin_group_object_ids) > 0
    error_message = "at least one Entra ID group must be given cluster-admin; see this module's own README."
  }
}

variable "postgres_admin_login" {
  description = "Administrator login for Azure Database for PostgreSQL Flexible Server. The password is not a variable -- see postgres_admin_password_secret_id's own description."
  type        = string
  default     = "astra_admin"
}

variable "aks_node_count" {
  description = "Default node pool size. Small by design -- this deploys services and workers (spec §5.3), not the estate's own compute; adjust for the programme's real concurrency."
  type        = number
  default     = 3
}

variable "aks_node_vm_size" {
  type    = string
  default = "Standard_D4s_v5"
}

variable "postgres_sku_name" {
  description = "Flexible Server compute tier/size, e.g. GP_Standard_D4s_v3 (General Purpose)."
  type        = string
  default     = "GP_Standard_D4s_v3"
}

variable "postgres_storage_mb" {
  type    = number
  default = 131072 # 128 GiB
}

variable "egress_allow_list_fqdns" {
  description = <<-EOT
    FQDNs the AKS cluster's own default-deny egress is allowed to reach (spec §5.3: "only
    broker-approved endpoints are reachable"). Real values depend on which model gateway
    provider and container registry the deployment uses -- these are the ones this
    codebase's own Model Gateway (gateway.py) and base images (Dockerfile) already name.
  EOT
  type        = list(string)
  default = [
    "api.anthropic.com",         # gateway.py's own AnthropicModelCaller (S5.3.2)
    "login.microsoftonline.com", # Entra ID token issuance and JWKS (entra.py)
    "graph.microsoft.com",       # Microsoft Graph, for group-membership overage (entra.py's own disclosed gap)
    "*.vault.azure.net",         # Key Vault (credentials.py's own KeyVaultCredentialProvider)
    "*.azurecr.io",              # the deployment's own container registry
    "*.blob.core.windows.net",   # artefact/BOM storage (artefacts.py, bom.py)
  ]
}

variable "postgres_admin_password_secret_name" {
  description = <<-EOT
    Name of a Key Vault secret that must already hold the PostgreSQL administrator
    password before the PostgreSQL resources in this module are applied. Deliberately not
    a `sensitive` string *value* variable: `credentials.py`'s own "a secret never crosses
    the API" discipline applies here too -- a value typed into a .tfvars file or a CI
    variable is not meaningfully different from one in a request log. This module reads
    the value with a `data` source once the secret exists (see this directory's own
    README for the two-phase `apply` this implies: the Key Vault first, then the secret
    seeded out of band, then everything else).
  EOT
  type        = string
  default     = "postgres-admin-password"
}

variable "console_redirect_uris" {
  description = "Where console-web's own Entra ID app registration (identity.tf) may redirect back to after sign-in -- the real URL(s) the console is served from. localhost is included by default so this module works against a local dev build out of the box."
  type        = list(string)
  default     = ["http://localhost:5173"]
}

variable "tags" {
  description = "Applied to every resource this module creates."
  type        = map(string)
  default     = {}
}
