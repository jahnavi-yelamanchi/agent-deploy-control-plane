output "cluster_name" {
  value = aws_eks_cluster.this.name
}

output "cluster_endpoint" {
  value = aws_eks_cluster.this.endpoint
}

output "oidc_provider_arn" {
  value = aws_iam_openid_connect_provider.eks.arn
}

# helm: serviceAccount.annotations."eks\.amazonaws\.com/role-arn"
output "runtime_role_arn" {
  value = aws_iam_role.runtime.arn
}

output "mlflow_artifact_bucket" {
  value = aws_s3_bucket.mlflow.bucket
}

output "ecr_repository_url" {
  value = aws_ecr_repository.runtime.repository_url
}

output "vpc_id" {
  value = aws_vpc.this.id
}

output "private_subnet_ids" {
  value = aws_subnet.private[*].id
}

# FinOps input (adcp.finops.report): instance type, count and hourly price per pool.
output "node_pools" {
  value = local.node_pools
}

locals {
  node_pools = merge(
    {
      cpu = {
        cloud         = "aws"
        instance_type = var.cpu_node_pool.instance_type
        count         = var.cpu_node_pool.desired
        hourly_usd    = lookup(var.node_hourly_usd, var.cpu_node_pool.instance_type, 0)
      }
    },
    var.enable_gpu_pool ? {
      gpu = {
        cloud         = "aws"
        instance_type = var.gpu_node_pool.instance_type
        count         = var.gpu_node_pool.desired
        hourly_usd    = lookup(var.node_hourly_usd, var.gpu_node_pool.instance_type, 0)
      }
    } : {}
  )
}
