# Explicit control-plane + kubelet identities: AcrPull principal is known before the cluster exists.
resource "azurerm_user_assigned_identity" "aks" {
  name                = "id-${var.name}-aks"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  tags                = local.tags
}

resource "azurerm_user_assigned_identity" "kubelet" {
  name                = "id-${var.name}-kubelet"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  tags                = local.tags
}

resource "azurerm_role_assignment" "aks_kubelet_operator" {
  scope                = azurerm_user_assigned_identity.kubelet.id
  role_definition_name = "Managed Identity Operator"
  principal_id         = azurerm_user_assigned_identity.aks.principal_id
}

resource "azurerm_kubernetes_cluster" "this" {
  name                = "aks-${var.name}"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  dns_prefix          = var.name
  kubernetes_version  = var.kubernetes_version

  # Entra workload identity: projected SA token federated with a user-assigned identity.
  oidc_issuer_enabled       = true
  workload_identity_enabled = true

  default_node_pool {
    name                 = "cpu"
    vm_size              = var.cpu_node_pool.vm_size
    auto_scaling_enabled = true
    min_count            = var.cpu_node_pool.min
    max_count            = var.cpu_node_pool.max
    node_count           = var.cpu_node_pool.count
    node_labels          = { pool = "cpu" }
    upgrade_settings {
      max_surge = "33%"
    }
  }

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.aks.id]
  }

  kubelet_identity {
    client_id                 = azurerm_user_assigned_identity.kubelet.client_id
    object_id                 = azurerm_user_assigned_identity.kubelet.principal_id
    user_assigned_identity_id = azurerm_user_assigned_identity.kubelet.id
  }

  network_profile {
    network_plugin      = "azure"
    network_plugin_mode = "overlay"
    network_policy      = "cilium"
    network_data_plane  = "cilium"
  }

  tags       = local.tags
  depends_on = [azurerm_role_assignment.aks_kubelet_operator]
}

resource "azurerm_kubernetes_cluster_node_pool" "gpu" {
  count                 = var.enable_gpu_pool ? 1 : 0
  name                  = "gpu"
  kubernetes_cluster_id = azurerm_kubernetes_cluster.this.id
  vm_size               = var.gpu_node_pool.vm_size
  auto_scaling_enabled  = true
  min_count             = var.gpu_node_pool.min
  max_count             = var.gpu_node_pool.max
  node_count            = var.gpu_node_pool.count
  node_labels           = { pool = "gpu" }
  node_taints           = ["nvidia.com/gpu=true:NoSchedule"]
  tags                  = local.tags
}

resource "azurerm_role_assignment" "kubelet_acr_pull" {
  scope                            = azurerm_container_registry.this.id
  role_definition_name             = "AcrPull"
  principal_id                     = azurerm_user_assigned_identity.kubelet.principal_id
  skip_service_principal_aad_check = true
}
