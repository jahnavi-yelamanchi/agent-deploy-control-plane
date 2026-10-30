locals {
  tags = merge({ project = var.name, managed-by = "terraform" }, var.tags)
  # Storage / ACR / Key Vault names: alphanumeric, globally unique.
  compact_name = substr(replace(lower(var.name), "/[^a-z0-9]/", ""), 0, 12)
}

resource "azurerm_resource_group" "this" {
  name     = "rg-${var.name}"
  location = var.location
  tags     = local.tags
}

resource "azurerm_container_registry" "this" {
  name                = "acr${local.compact_name}"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  sku                 = "Standard"
  admin_enabled       = false
  tags                = local.tags
}

# MLflow artifact store.
resource "azurerm_storage_account" "mlflow" {
  name                            = "st${local.compact_name}mlflow"
  resource_group_name             = azurerm_resource_group.this.name
  location                        = azurerm_resource_group.this.location
  account_tier                    = "Standard"
  account_replication_type        = "ZRS"
  min_tls_version                 = "TLS1_2"
  allow_nested_items_to_be_public = false
  shared_access_key_enabled       = false
  blob_properties {
    versioning_enabled = true
  }
  tags = local.tags
}

resource "azurerm_storage_container" "mlflow" {
  name                  = "mlflow-artifacts"
  storage_account_id    = azurerm_storage_account.mlflow.id
  container_access_type = "private"
}
