# Setup

## Prerequisites

| For | Needs |
|---|---|
| `make install`, `make demo-local`, `make test`, `make eval-gate` | Python >= 3.12 (3.13 used), [uv](https://docs.astral.sh/uv/), make |
| `make promptfoo` | Node 22 (`npx promptfoo@0.103.5`) |
| `make up`, `make demo` | Docker, kind, kubectl, helm, kubectl-argo-rollouts plugin |
| `make validate-k8s` | helm, kustomize, kubeconform |
| `make plan-aws`, `make plan-azure` | Terraform >= 1.7 (CI: 1.9.8); providers hashicorp/aws 6.71.0, hashicorp/azurerm 4.81.0 |
| `make infracost` | Infracost CLI + API key (optional; falls back to static table) |

## Local quickstart (no keys)

```bash
make install        # uv sync --extra dev --extra evals
make demo-local     # register -> gate -> canary -> Pareto -> rollback / needs_human, in-process
make test
make eval-gate      # Inspect AI suite + thresholds on specs/v1.yaml
make report         # runs/*.jsonl -> results/results.md + results/cost_vs_quality.png
```

`MODEL_PROVIDER=mock` is the default (Makefile, `.env.example`, `adcp.config`). `BACKEND=local` uses SQLite, in-process bus, in-memory metrics and the flagd in-process resolver.

Full local stack:

```bash
make up             # kind + Argo CD + Rollouts + Istio + kube-prometheus-stack + Tempo + Loki, compose data plane
make demo           # v1 stable; v2-bad canary -> rollback; v3-tradeoff canary -> needs_human
make down
```

`make demo` falls back to `make demo-local` when no `kind-adcp` context exists.

## Env vars for real providers

Copy `.env.example` to `.env`. Only read when `MODEL_PROVIDER=real` (any value other than `mock`/empty). Provider is chosen per spec by `provider:` in the AgentSpec.

| Provider | Var | Read by | Notes |
|---|---|---|---|
| Anthropic | `ANTHROPIC_API_KEY` | anthropic SDK (`providers/anthropic_provider.py`) | |
| OpenAI | `OPENAI_API_KEY` | `providers/openai_compat.py` | Also sent as bearer to vLLM/SGLang if set, else `EMPTY` |
| Google | `GOOGLE_API_KEY` | google-genai SDK (`providers/google_provider.py`) | Vertex instead: `GOOGLE_GENAI_USE_VERTEXAI=true` |
| AWS Bedrock | `AWS_REGION` | `providers/bedrock_provider.py` | Default `us-east-1` |
| | `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY`, or `AWS_PROFILE` | boto3 / AWS default chain | IRSA on EKS (`AWS_ROLE_ARN`). `bootstrap.sh` accepts keys, profile or role |
| | Bedrock model id | spec `model_id` | No env var. Adapter prefixes `anthropic.`; AWS overlay sets `anthropic.claude-sonnet-5-5`. Enable model access in the Bedrock console |
| Azure OpenAI | `AZURE_OPENAI_ENDPOINT` | `providers/openai_compat.py` (`AzureOpenAIProvider`) | Takes precedence over `AZURE_AI_ENDPOINT` |
| | `AZURE_OPENAI_DEPLOYMENT`, `AZURE_OPENAI_API_VERSION` | same | Defaults: model id minus `azure/`; `2025-04-01-preview` |
| | `AZURE_OPENAI_API_KEY` | `providers/openai_compat.py` | Optional; unset -> Entra ID token (`DefaultAzureCredential`) |
| Azure AI Foundry | `AZURE_AI_ENDPOINT` | `providers/azure_provider.py` | Code name; `AZURE_AI_FOUNDRY_ENDPOINT` is not read |
| Azure identity | `AZURE_CLIENT_ID`, `AZURE_TENANT_ID` | azure-identity `DefaultAzureCredential` | Required by `bootstrap.sh` unless `AZURE_OPENAI_API_KEY` is set |
| | `AZURE_FEDERATED_TOKEN_FILE` | azure-identity (workload identity) | Injected by AKS; not set by hand |
| | `AZURE_CLIENT_SECRET` | azure-identity (`EnvironmentCredential`) | Local service principal alternative; or `az login` |
| | `AZURE_SUBSCRIPTION_ID` | not read | Terraform azurerm reads `ARM_SUBSCRIPTION_ID` |
| vLLM | `VLLM_BASE_URL` | `providers/openai_compat.py` | e.g. `http://localhost:8000/v1`; counted by `bootstrap.sh` |
| SGLang | `SGLANG_BASE_URL` | same | e.g. `http://localhost:30000/v1`; counted by `bootstrap.sh` |
| Langfuse | `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` | `bootstrap.sh` check; compose init | Used to build `LANGFUSE_AUTH` |
| | `LANGFUSE_AUTH` | OTel Collector (`infra/observability/otel-collector.yaml`) | `printf '%s:%s' "$LANGFUSE_PUBLIC_KEY" "$LANGFUSE_SECRET_KEY" \| base64 -w0`; compose default matches local keys |
| | `LANGFUSE_AUTH` | `infra/observability/otel-collector.yaml` | base64(public:secret); exporter endpoint fixed to `http://langfuse-web:3000/api/public/otel`, edit for Langfuse Cloud |
| GitHub | `GITHUB_TOKEN` | `adcp.config` -> `rollback/github.py` | Issue scope on `GITHUB_REPO` (default `jahnavi-yelamanchi/agent-deploy-control-plane`); unset -> issues written to `runs/issues/*.md` |

Other runtime vars (`BACKEND`, `MLFLOW_TRACKING_URI`, `KAFKA_BOOTSTRAP`, `FLAGD_HOST`, `OTEL_EXPORTER_OTLP_ENDPOINT`, `PROMETHEUS_URL`, `ADCP_SPEC_ALIAS`, `ADCP_ROLLBACK_RULE`, `ADCP_ROLLBACK_INTERVAL_S`): see `.env.example`, `src/adcp/config.py`, `src/adcp/rollback/service.py`.

## Mock -> real: `make bootstrap`

```bash
make bootstrap                   # ./scripts/bootstrap.sh
./scripts/bootstrap.sh --record  # also re-record fixtures from real providers
```

1. No `.env`: copies `.env.example`, exits 1. Fill keys, re-run.
2. Sources `.env`; prints ok / missing per provider (anthropic, openai, google, aws_bedrock, azure, langfuse).
3. Zero providers configured: stays on mock, exits 1.
4. `uv sync --extra dev --extra evals --extra providers` (anthropic, openai, google-genai, boto3, azure-ai-inference, azure-identity).
5. Sets `MODEL_PROVIDER=real` in `.env`.
6. `--record`: `scripts/record_fixtures.py` writes `src/adcp/providers/fixtures/mock/<model_id>.jsonl` from live calls.

`.env` is not loaded into the process environment: `get_provider` reads `os.getenv("MODEL_PROVIDER")`, the SDKs read their keys from the environment, and the Makefile exports `MODEL_PROVIDER ?= mock`. Export `.env` in the shell before running targets:

```bash
set -a; source .env; set +a
uv run python -m adcp.evals.matrix --out runs/matrix.jsonl && make report
```

Back to mock: `export MODEL_PROVIDER=mock` (and set it in `.env`).

## Cloud

Checked without credentials (mock providers, `command = plan`):

```bash
make plan-aws
make plan-azure
```

Neither module has a `backend` block; add one before a shared apply.

### AWS (EKS, IRSA, ECR, S3, Bedrock IAM)

```bash
export AWS_PROFILE=<profile> AWS_REGION=us-east-1
cd infra/terraform/aws
# add: terraform { backend "s3" {} } to versions.tf
terraform init -backend-config="bucket=<state-bucket>" -backend-config="key=adcp/eks.tfstate" -backend-config="region=$AWS_REGION"
terraform plan -out tfplan -var "region=$AWS_REGION"   # also: bedrock_model_arns, eks_oidc_thumbprint, enable_gpu_pool
terraform apply tfplan
aws eks update-kubeconfig --name "$(terraform output -raw cluster_name)"
```

Copy outputs into `infra/deploy/kustomize/overlays/aws/agent-runtime-values.yaml`: `runtime_role_arn` -> `serviceAccount.awsRoleArn`, `ecr_repository_url` -> `image.repository`, `mlflow_artifact_bucket`.

### Azure (AKS, workload identity, ACR, Azure OpenAI, AI Foundry)

```bash
az login
export ARM_SUBSCRIPTION_ID=<subscription> TF_VAR_tenant_id=<tenant>
cd infra/terraform/azure
# add: terraform { backend "azurerm" {} } to versions.tf
terraform init -backend-config="resource_group_name=<rg>" -backend-config="storage_account_name=<sa>" -backend-config="container_name=tfstate" -backend-config="key=adcp/aks.tfstate"
terraform plan -out tfplan
terraform apply tfplan
az aks get-credentials -g "$(terraform output -raw resource_group)" -n "$(terraform output -raw cluster_name)"
```

Copy outputs into `infra/deploy/kustomize/overlays/azure/agent-runtime-values.yaml`: `runtime_client_id` -> `serviceAccount.azureClientId`, `acr_login_server` -> `image.repository`, `openai_endpoint` -> `AZURE_OPENAI_ENDPOINT`, `openai_deployment` -> `AZURE_OPENAI_DEPLOYMENT`.

Then point Argo CD at the overlay (`infra/deploy/argocd/root.yaml`) and push the image to ECR / ACR.

## GitHub Actions settings

| Name | Kind | Used in | Effect |
|---|---|---|---|
| `PUSH_IMAGE` | repo variable | `release.yml` | `true`: log in to GHCR and push `ghcr.io/<owner>/agent-runtime:<sha>` |
| `GITOPS_PUSH` | repo variable | `release.yml` | `true`: commit the Helm values bump and push to `main` |
| `GITOPS_DEPLOY_KEY` | secret | `release.yml` | SSH deploy key with write access; needed with `GITOPS_PUSH` |
| `MLFLOW_TRACKING_URI` | repo variable | `release.yml` | Registry for `adcp register --stage candidate`; default local sqlite (uploaded as artifact) |
| `NIGHTLY_PROVIDER` | repo variable | `nightly-eval.yml` | Sets `MODEL_PROVIDER`; default `mock` |
| `GITHUB_TOKEN` | built-in | `release.yml` | GHCR login (`packages: write`) |

`NIGHTLY_PROVIDER=real` also needs provider keys mapped from secrets into the job env and `uv sync --extra providers`; `nightly-eval.yml` does neither yet.
