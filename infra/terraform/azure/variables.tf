variable "name" {
  type    = string
  default = "adcp"
}

variable "location" {
  type    = string
  default = "eastus2"
}

variable "tenant_id" {
  description = "Entra tenant for Key Vault. Set via TF_VAR_tenant_id."
  type        = string
  default     = "00000000-0000-0000-0000-000000000000"
}

variable "kubernetes_version" {
  type    = string
  default = "1.33"
}

variable "cpu_node_pool" {
  type = object({
    vm_size = string
    min     = number
    count   = number
    max     = number
  })
  default = { vm_size = "Standard_D4s_v5", min = 2, count = 3, max = 6 }
}

variable "enable_gpu_pool" {
  description = "GPU node pool for vLLM / SGLang self-hosted serving."
  type        = bool
  default     = false
}

variable "gpu_node_pool" {
  type = object({
    vm_size = string
    min     = number
    count   = number
    max     = number
  })
  default = { vm_size = "Standard_NC4as_T4_v3", min = 0, count = 1, max = 2 }
}

# Pay-as-you-go USD/hour, eastus2. Read by adcp.finops.report via `terraform output -json node_pools`.
variable "node_hourly_usd" {
  type = map(number)
  default = {
    "Standard_D4s_v5"          = 0.192
    "Standard_D8s_v5"          = 0.384
    "Standard_NC4as_T4_v3"     = 0.526
    "Standard_NC24ads_A100_v4" = 3.673
  }
}

variable "runtime_namespace" {
  type    = string
  default = "agents"
}

variable "runtime_service_account" {
  type    = string
  default = "agent-runtime"
}

variable "openai_deployment_name" {
  type    = string
  default = "gpt-5"
}

variable "openai_model_name" {
  type    = string
  default = "gpt-5"
}

variable "openai_model_version" {
  type    = string
  default = "2025-08-07"
}

variable "openai_sku" {
  type    = string
  default = "GlobalStandard"
}

variable "openai_capacity" {
  description = "Thousands of tokens per minute."
  type        = number
  default     = 50
}

variable "tags" {
  type    = map(string)
  default = {}
}
