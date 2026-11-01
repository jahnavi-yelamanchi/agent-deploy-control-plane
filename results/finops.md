# FinOps

## Per-deploy token cost (frozen task set, mock provider, `finops/prices.yaml`)

| spec | model | provider | cache | cost/task $ | $/1k tasks | tokens in/out per task | cache hit | eval score | p95 ms |
|---|---|---|---|---:|---:|---:|---:|---:|---:|
| v1 (v1) | claude-sonnet-5-5 | anthropic | on | 0.00384 | 3.84 | 5447/195 | 0.92 | 0.924 | 5399 |
| v2-bad (v2) | claude-sonnet-5-5 | anthropic | off | 0.01932 | 19.32 | 8154/301 | 0.00 | 0.801 | 7102 |
| v3-tradeoff (v3) | claude-haiku-5-5 | anthropic | on | 0.00018 | 0.18 | 5447/174 | 0.92 | 0.875 | 2777 |

Cluster amortized at 1,000,000 tasks/month: EKS $0.53 / 1k tasks, AKS $0.44 / 1k tasks (add to the token cost above).

## Cluster cost: EKS vs AKS (Terraform node pool defaults)

| cloud | item | type | count | $/h | $/month |
|---|---|---|---:|---:|---:|
| aws | EKS control plane | eks_standard | 1 | 0.100 | 73.00 |
| aws | cpu node pool | m6i.xlarge | 3 | 0.1920 | 420.48 |
| aws | gpu node pool (disabled by default) | g6.xlarge | 1 | 0.8048 | 587.50 |
| aws | aws_nat_gateway | - | 1 | 0.0450 | 32.85 |
| azure | AKS control plane (Free tier) | aks_free | 1 | 0.000 | 0.00 |
| azure | cpu node pool | Standard_D4s_v5 | 3 | 0.1920 | 420.48 |
| azure | gpu node pool (disabled by default) | Standard_NC4as_T4_v3 | 1 | 0.5260 | 383.98 |
| azure | azurerm_container_registry | - | 1 | 0.0274 | 20.00 |

| total $/month | EKS | AKS |
|---|---:|---:|
| CPU pool + control plane + extras | 526.33 | 440.48 |
| + GPU node pool (vLLM/SGLang) | 1,113.83 | 824.46 |

AKS Standard tier (uptime SLA) adds $73.00/month. GPU SKUs differ (L4 vs T4). Excludes egress, storage, ECR, load balancers, model API spend.

Source: Static price table `src/adcp/finops/infra_prices.yaml` (as of 2026-10); `infracost` not on PATH.
