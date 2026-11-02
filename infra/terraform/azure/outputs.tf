output "resource_group" {
  value = azurerm_resource_group.this.name
}

output "cluster_name" {
  value = azurerm_kubernetes_cluster.this.name
}

output "oidc_issuer_url" {
  value = azurerm_kubernetes_cluster.this.oidc_issuer_url
}

# helm: serviceAccount.annotations."azure\.workload\.identity/client-id"
output "runtime_client_id" {
  value = azurerm_user_assigned_identity.runtime.client_id
}

output "acr_login_server" {
  value = azurerm_container_registry.this.login_server
}

output "openai_endpoint" {
  value = azurerm_cognitive_account.openai.endpoint
}

output "openai_deployment" {
  value = azurerm_cognitive_deployment.chat.name
}

output "ai_foundry_project_id" {
  value = azurerm_ai_foundry_project.agents.id
}

output "mlflow_artifact_uri" {
  value = "wasbs://${azurerm_storage_container.mlflow.name}@${azurerm_storage_account.mlflow.name}.blob.core.windows.net/"
}

# FinOps input (adcp.finops.report): instance type, count and hourly price per pool.
output "node_pools" {
  value = local.node_pools
}

locals {
  node_pools = merge(
    {
      cpu = {
        cloud         = "azure"
        instance_type = var.cpu_node_pool.vm_size
        count         = var.cpu_node_pool.count
        hourly_usd    = lookup(var.node_hourly_usd, var.cpu_node_pool.vm_size, 0)
      }
    },
    var.enable_gpu_pool ? {
      gpu = {
        cloud         = "azure"
        instance_type = var.gpu_node_pool.vm_size
        count         = var.gpu_node_pool.count
        hourly_usd    = lookup(var.node_hourly_usd, var.gpu_node_pool.vm_size, 0)
      }
    } : {}
  )
}
