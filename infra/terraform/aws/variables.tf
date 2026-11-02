variable "name" {
  type    = string
  default = "adcp"
}

variable "region" {
  type    = string
  default = "us-east-1"
}

# Explicit AZs: no aws_availability_zones data source so plan works offline.
variable "azs" {
  type    = list(string)
  default = ["us-east-1a", "us-east-1b", "us-east-1c"]
}

variable "vpc_cidr" {
  type    = string
  default = "10.40.0.0/16"
}

variable "kubernetes_version" {
  type    = string
  default = "1.33"
}

variable "cpu_node_pool" {
  type = object({
    instance_type = string
    min           = number
    desired       = number
    max           = number
  })
  default = { instance_type = "m6i.xlarge", min = 2, desired = 3, max = 6 }
}

variable "enable_gpu_pool" {
  description = "GPU node group for vLLM / SGLang self-hosted serving."
  type        = bool
  default     = false
}

variable "gpu_node_pool" {
  type = object({
    instance_type = string
    min           = number
    desired       = number
    max           = number
  })
  default = { instance_type = "g6.xlarge", min = 0, desired = 1, max = 2 }
}

# On-demand USD/hour, us-east-1. Read by adcp.finops.report via `terraform output -json node_pools`.
variable "node_hourly_usd" {
  type = map(number)
  default = {
    "m6i.xlarge"  = 0.192
    "m6i.2xlarge" = 0.384
    "g6.xlarge"   = 0.8048
    "g5.xlarge"   = 1.006
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

variable "bedrock_model_arns" {
  description = "Foundation model / inference profile ARNs the runtime may invoke."
  type        = list(string)
  default = [
    "arn:aws:bedrock:*::foundation-model/anthropic.*",
    "arn:aws:bedrock:*:*:inference-profile/*",
  ]
}

variable "eks_oidc_thumbprint" {
  description = "Root CA thumbprint for oidc.eks.<region>.amazonaws.com."
  type        = string
  default     = "9e99a48a9960b14926bb7f3b02e22da2b0ab7280"
}

variable "ecr_keep_images" {
  type    = number
  default = 30
}

variable "tags" {
  type    = map(string)
  default = {}
}
