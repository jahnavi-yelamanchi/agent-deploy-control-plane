# Keyword map

| Keyword | Path(s) | Usage |
|---|---|---|
| Kubernetes | `deploy/helm/rollback-controller/templates` | Deployment/Service/RBAC manifests for runtime and controller |
| kind | `deploy/kind/kind-config.yaml` | local cluster definition used by `make up` |
| kind | `scripts/up.sh` | creates the cluster |
| EKS | `terraform/aws/eks.tf` | EKS cluster + managed node groups |
| AKS | `terraform/azure/aks.tf` | AKS with OIDC issuer and workload identity |
| Argo CD | `deploy/argocd/root.yaml` | app-of-apps root Application |
| Argo CD | `deploy/argocd/project.yaml` | AppProject scoping repos and namespaces |
| Argo Rollouts | `deploy/helm/agent-runtime/templates/rollout.yaml` | canary 10/25/50/100 Rollout |
| Argo Rollouts | `src/adcp/rollback/argo.py` | controller aborts/promotes via Rollout status merge-patch |
| Istio | `deploy/helm/agent-runtime/templates/rollout.yaml` | Rollout routes canary weight through Istio |
| Helm | `deploy/helm/agent-runtime/Chart.yaml` | runtime chart; also deploy/helm/rollback-controller |
| Kustomize | `deploy/kustomize/overlays/aws/kustomization.yaml` | overlays local/aws/azure patch IRSA/workload identity and image |
| Terraform | `terraform/aws/versions.tf` | pinned providers; tests/plan.tftest.hcl plans with mock_provider |
| MLflow Model Registry | `src/adcp/registry.py` | AgentSpec versions with candidate/canary/production aliases |
| MinIO | `compose/docker-compose.yaml` | S3-compatible MLflow artifact store |
| GitHub Actions | `.github/workflows/ci.yml` | ci, eval-gate, release on push to main; nightly-eval on schedule |
| OpenTelemetry | `src/adcp/runtime/telemetry.py` | tracer + meter, OTLP exporter |
| OTel Collector | `observability/otel-collector.yaml` | fans out traces/metrics/logs to Tempo, Prometheus, Loki, Langfuse |
| GenAI semantic conventions | `src/adcp/runtime/telemetry.py` | gen_ai.* span attributes and gen_ai.client.* histograms |
| Prometheus | `observability/prometheus/rules/recording.yml` | canary vs stable recording rules queried by AnalysisTemplates and controller |
| Grafana | `observability/grafana/dashboards/agent-overview.json` | provisioned dashboard: cost/task, p50/p95, eval score, cache hit, rollbacks, burn rate |
| Grafana Tempo | `observability/tempo.yaml` | trace backend with metrics generator |
| Loki | `observability/loki.yaml` | log backend, OTLP ingest |
| Langfuse | `observability/otel-collector.yaml` | otlphttp/langfuse exporter; Langfuse v3 in compose |
| Inspect AI | `src/adcp/evals/inspect_suite.py` | tau task, solver over harness, state-based scorer |
| promptfoo | `evals/promptfoo/promptfooconfig.yaml` | prompt regression with assertions via Python provider |
| vLLM | `deploy/serving/ray/serve_vllm.py` | vLLM engine in Ray Serve with prefix caching |
| SGLang | `deploy/serving/sglang/deployment.yaml` | SGLang server with radix cache; adapter in providers/openai_compat.py |
| Ray Serve | `deploy/serving/ray/serve_vllm.py` | Ray Serve deployment; rayservice.yaml for KubeRay |
| KServe | `deploy/serving/kserve/inferenceservice.yaml` | InferenceService for the same Llama model |
| Redis | `src/adcp/runtime/budget.py` | cost/latency budget counters and token-bucket rate limit |
| Postgres | `src/adcp/runtime/store.py` | runs and eval_results tables |
| Kafka (Redpanda) | `src/adcp/runtime/events.py` | agent.runs/agent.evals/agent.rollbacks topics; Redpanda in compose |
| SLO | `slo/slo.yaml` | availability, latency, quality, cost SLOs |
| error budget | `scripts/gen_slo_rules.py` | slo:error_budget:remaining recording rules |
| burn-rate alert | `observability/prometheus/rules/slo-generated.yml` | multiwindow multi-burn-rate page/ticket alerts |
| canary | `deploy/helm/agent-runtime/values.yaml` | canary steps; simulated in src/adcp/rollout/sim.py |
| blue-green | `deploy/helm/agent-runtime/templates/rollout.yaml` | alternative Rollout strategy with preview analysis |
| feature flags | `flags/flags.flagd.json` | gate new tool and context strategy per track/rolloutStep |
| OpenFeature | `src/adcp/runtime/flags.py` | OpenFeature client evaluation |
| flagd | `src/adcp/runtime/flags.py` | flagd provider (RPC or in-process file) |
| chaos engineering | `src/adcp/chaos/faults.py` | prompt degradation, latency, killed tool injectors |
| LitmusChaos | `chaos/litmus/engines.yaml` | pod-delete and network-latency on canary pods with promProbes |
| FinOps | `src/adcp/finops/report.py` | per-deploy, per-1k-task and EKS-vs-AKS cost tables |
| Infracost | `infracost.yml` | config for both Terraform projects; scripts/infracost.sh |
| AWS Bedrock | `src/adcp/providers/bedrock_provider.py` | Bedrock adapter; IAM policy in terraform/aws/iam_irsa.tf |
| Azure AI Foundry | `terraform/azure/ai.tf` | Foundry hub + project; adapter src/adcp/providers/azure_provider.py |
| Azure OpenAI | `terraform/azure/ai.tf` | cognitive account kind OpenAI + model deployment |
| Entra workload identity | `terraform/azure/identity.tf` | federated credential for runtime service account |
| IRSA | `terraform/aws/iam_irsa.tf` | OIDC-trusted role for runtime service account |
| ECR | `terraform/aws/storage.tf` | image registry with scan on push |
| ACR | `terraform/azure` | image registry + AcrPull for kubelet |
| prompt caching | `src/adcp/providers/anthropic_provider.py` | ephemeral cache breakpoints on by default |
| KV cache | `deploy/serving/kserve/inferenceservice.yaml` | vLLM KV cache dtype and memory settings |
| prefix caching | `deploy/serving/kserve/inferenceservice.yaml` | vLLM prefix caching flag |
| cache hit rate | `observability/prometheus/rules/recording.yml` | adcp:cache_hit_ratio and vllm:prefix_cache_hit_ratio rules |
| LangGraph | `src/adcp/runtime/agent.py` | plan -> call_model -> call_tools -> finalize graph |
| LangChain Deep Agents | `src/adcp/runtime/deep_agent.py` | optional builder over the same MCP tools |
| MCP | `src/adcp/runtime/mcp_server.py` | tau tool server; client in runtime/mcp_client.py |
| Pydantic | `src/adcp/spec.py` | AgentSpec and all run/eval models |
| FastAPI | `src/adcp/runtime/app.py` | /run, /healthz, /readyz, /spec; controller in rollback/service.py |
| tau-bench | `src/adcp/env/tools.py` | airline/retail tools with policy rules; 50 tasks in evals/tasks/tasks.jsonl |
| Pareto | `src/adcp/rollback/pareto.py` | dominance check on cost, p95, eval score |
| multi-objective rollback | `src/adcp/rollback/controller.py` | acts on Pareto decision: abort, promote, or GitHub issue |
| GitOps | `deploy/argocd/apps/agent-runtime.yaml` | Argo CD syncs Helm values bumped by release.yml |
| AnalysisTemplate | `deploy/helm/agent-runtime/templates/analysistemplates.yaml` | Prometheus queries for eval score, p95, cost, errors |
| VirtualService | `deploy/helm/agent-runtime/templates/istio.yaml` | weighted stable/canary routes |
| Mermaid | `docs/ARCHITECTURE.md` | rollout + rollback loop flowchart and canary sequence |
| ADR | `docs/ADR-006-pareto-rollback.md` | 21 ADRs in docs/ |
