# IRSA: EKS OIDC issuer -> IAM OIDC provider -> role assumable by agents/agent-runtime SA.
locals {
  oidc_issuer_url  = aws_eks_cluster.this.identity[0].oidc[0].issuer
  oidc_issuer_host = replace(local.oidc_issuer_url, "https://", "")
}

resource "aws_iam_openid_connect_provider" "eks" {
  url             = local.oidc_issuer_url
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = [var.eks_oidc_thumbprint]
}

resource "aws_iam_role" "runtime" {
  name = "${var.name}-agent-runtime"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = aws_iam_openid_connect_provider.eks.arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "${local.oidc_issuer_host}:sub" = "system:serviceaccount:${var.runtime_namespace}:${var.runtime_service_account}"
          "${local.oidc_issuer_host}:aud" = "sts.amazonaws.com"
        }
      }
    }]
  })
}

# Bedrock policy built only from variables so it is known at plan time.
resource "aws_iam_policy" "bedrock" {
  name        = "${var.name}-agent-runtime-bedrock"
  description = "AWS Bedrock invoke for the agent runtime."
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid      = "BedrockInvoke"
      Effect   = "Allow"
      Action   = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
      Resource = var.bedrock_model_arns
    }]
  })
}

resource "aws_iam_policy" "mlflow" {
  name        = "${var.name}-agent-runtime-mlflow"
  description = "MLflow artifact read/write."
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid      = "MlflowArtifacts"
      Effect   = "Allow"
      Action   = ["s3:GetObject", "s3:PutObject", "s3:ListBucket"]
      Resource = [aws_s3_bucket.mlflow.arn, "${aws_s3_bucket.mlflow.arn}/*"]
    }]
  })
}

resource "aws_iam_role_policy_attachment" "runtime" {
  for_each = {
    bedrock = aws_iam_policy.bedrock.arn
    mlflow  = aws_iam_policy.mlflow.arn
  }
  role       = aws_iam_role.runtime.name
  policy_arn = each.value
}
