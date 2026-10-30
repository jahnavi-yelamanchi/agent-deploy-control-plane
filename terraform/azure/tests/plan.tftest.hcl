mock_provider "azurerm" {}

variables {
  name     = "adcptest"
  location = "eastus2"
}

run "plan_default" {
  command = plan

  assert {
    condition     = azurerm_kubernetes_cluster.this.oidc_issuer_enabled && azurerm_kubernetes_cluster.this.workload_identity_enabled
    error_message = "AKS needs OIDC issuer + workload identity"
  }

  assert {
    condition     = azurerm_federated_identity_credential.runtime.subject == "system:serviceaccount:agents:agent-runtime"
    error_message = "federated credential subject must match runtime SA"
  }

  assert {
    condition     = azurerm_role_assignment.runtime_openai_user.role_definition_name == "Cognitive Services OpenAI User"
    error_message = "workload identity must have OpenAI User role"
  }

  assert {
    condition     = azurerm_role_assignment.kubelet_acr_pull.role_definition_name == "AcrPull"
    error_message = "kubelet identity must pull from ACR"
  }

  assert {
    condition     = azurerm_cognitive_account.openai.kind == "OpenAI" && !azurerm_cognitive_account.openai.local_auth_enabled
    error_message = "Azure OpenAI account, keyless"
  }

  assert {
    condition     = azurerm_cognitive_deployment.chat.model[0].name == "gpt-5"
    error_message = "chat deployment model"
  }

  assert {
    condition     = length(azurerm_kubernetes_cluster_node_pool.gpu) == 0
    error_message = "GPU pool off by default"
  }

  assert {
    condition     = output.node_pools.cpu.hourly_usd > 0
    error_message = "node pricing map must cover the CPU VM size"
  }
}

run "plan_gpu_and_model_override" {
  command = plan

  variables {
    enable_gpu_pool      = true
    openai_model_name    = "gpt-5-mini"
    openai_model_version = "2025-08-07"
  }

  assert {
    condition     = length(azurerm_kubernetes_cluster_node_pool.gpu) == 1
    error_message = "GPU pool for vLLM"
  }

  assert {
    condition     = azurerm_cognitive_deployment.chat.model[0].name == "gpt-5-mini"
    error_message = "model name is a variable"
  }
}
