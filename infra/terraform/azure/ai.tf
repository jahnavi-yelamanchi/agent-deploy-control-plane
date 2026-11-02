# Azure OpenAI: keyless (local auth off), accessed via workload identity.
resource "azurerm_cognitive_account" "openai" {
  name                  = "oai-${var.name}"
  resource_group_name   = azurerm_resource_group.this.name
  location              = azurerm_resource_group.this.location
  kind                  = "OpenAI"
  sku_name              = "S0"
  custom_subdomain_name = "oai-${var.name}"
  local_auth_enabled    = false
  tags                  = local.tags
}

resource "azurerm_cognitive_deployment" "chat" {
  name                 = var.openai_deployment_name
  cognitive_account_id = azurerm_cognitive_account.openai.id
  model {
    format  = "OpenAI"
    name    = var.openai_model_name
    version = var.openai_model_version
  }
  sku {
    name     = var.openai_sku
    capacity = var.openai_capacity
  }
  version_upgrade_option = "NoAutoUpgrade"
}

# Azure AI Foundry hub + project. Hub requires Key Vault + storage.
resource "azurerm_key_vault" "foundry" {
  name                       = "kv-${local.compact_name}-fdry"
  resource_group_name        = azurerm_resource_group.this.name
  location                   = azurerm_resource_group.this.location
  tenant_id                  = var.tenant_id
  sku_name                   = "standard"
  rbac_authorization_enabled = true
  purge_protection_enabled   = true
  soft_delete_retention_days = 7
  tags                       = local.tags
}

resource "azurerm_storage_account" "foundry" {
  name                     = "st${local.compact_name}fdry"
  resource_group_name      = azurerm_resource_group.this.name
  location                 = azurerm_resource_group.this.location
  account_tier             = "Standard"
  account_replication_type = "LRS"
  min_tls_version          = "TLS1_2"
  tags                     = local.tags
}

resource "azurerm_ai_foundry" "hub" {
  name                  = "aih-${var.name}"
  resource_group_name   = azurerm_resource_group.this.name
  location              = azurerm_resource_group.this.location
  key_vault_id          = azurerm_key_vault.foundry.id
  storage_account_id    = azurerm_storage_account.foundry.id
  container_registry_id = azurerm_container_registry.this.id
  identity {
    type = "SystemAssigned"
  }
  tags = local.tags
}

resource "azurerm_ai_foundry_project" "agents" {
  name               = "aip-${var.name}-agents"
  location           = azurerm_ai_foundry.hub.location
  ai_services_hub_id = azurerm_ai_foundry.hub.id
  friendly_name      = "agent deploy control plane"
  identity {
    type = "SystemAssigned"
  }
  tags = local.tags
}
