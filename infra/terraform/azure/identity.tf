# Entra workload identity for agents/agent-runtime.
resource "azurerm_user_assigned_identity" "runtime" {
  name                = "id-${var.name}-agent-runtime"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  tags                = local.tags
}

resource "azurerm_federated_identity_credential" "runtime" {
  name      = "fic-${var.name}-agent-runtime"
  parent_id = azurerm_user_assigned_identity.runtime.id
  audience  = ["api://AzureADTokenExchange"]
  issuer    = azurerm_kubernetes_cluster.this.oidc_issuer_url
  subject   = "system:serviceaccount:${var.runtime_namespace}:${var.runtime_service_account}"
}

resource "azurerm_role_assignment" "runtime_openai_user" {
  scope                = azurerm_cognitive_account.openai.id
  role_definition_name = "Cognitive Services OpenAI User"
  principal_id         = azurerm_user_assigned_identity.runtime.principal_id
}

resource "azurerm_role_assignment" "runtime_mlflow_blob" {
  scope                = azurerm_storage_account.mlflow.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_user_assigned_identity.runtime.principal_id
}
