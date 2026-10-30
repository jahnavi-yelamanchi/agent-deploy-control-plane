mock_provider "aws" {}

variables {
  name   = "adcp-test"
  region = "us-east-1"
  azs    = ["us-east-1a", "us-east-1b"]
}

run "plan_cpu_only" {
  command = plan

  assert {
    condition     = length(aws_subnet.private) == 2 && length(aws_subnet.public) == 2
    error_message = "one private + one public subnet per AZ"
  }

  assert {
    condition     = length(aws_eks_node_group.gpu) == 0
    error_message = "GPU pool must be off by default"
  }

  assert {
    condition     = aws_ecr_repository.runtime.image_scanning_configuration[0].scan_on_push
    error_message = "ECR scan on push"
  }

  assert {
    condition     = aws_s3_bucket_versioning.mlflow.versioning_configuration[0].status == "Enabled"
    error_message = "MLflow bucket versioning"
  }

  assert {
    condition     = aws_s3_bucket_public_access_block.mlflow.block_public_acls && aws_s3_bucket_public_access_block.mlflow.restrict_public_buckets
    error_message = "MLflow bucket must block public access"
  }

  assert {
    condition = contains(
      jsondecode(aws_iam_policy.bedrock.policy).Statement[0].Action,
      "bedrock:InvokeModelWithResponseStream"
    )
    error_message = "runtime IRSA policy must allow Bedrock streaming invoke"
  }

  assert {
    condition     = output.node_pools.cpu.hourly_usd > 0
    error_message = "node pricing map must cover the CPU instance type"
  }
}

run "plan_with_gpu" {
  command = plan

  variables {
    enable_gpu_pool = true
  }

  assert {
    condition     = length(aws_eks_node_group.gpu) == 1 && aws_eks_node_group.gpu[0].ami_type == "AL2023_x86_64_NVIDIA"
    error_message = "GPU pool for vLLM"
  }

  assert {
    condition     = output.node_pools.gpu.instance_type == "g6.xlarge"
    error_message = "node_pools output lists GPU pool"
  }
}
