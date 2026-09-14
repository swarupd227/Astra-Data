terraform {
  required_version = ">= 1.9.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.5"
    }
    azuread = {
      source  = "hashicorp/azuread"
      version = "~> 3.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # Story S11.1.1's own scope is "local validate only" -- no backend is configured here,
  # so `terraform init` uses the default local state. A real deployment run from the
  # client's own Cloud Shell should configure a remote backend (an `azurerm_storage_
  # account` outside this module, so state does not depend on the infrastructure it
  # describes) before the first `apply` -- see this directory's own README.
}
