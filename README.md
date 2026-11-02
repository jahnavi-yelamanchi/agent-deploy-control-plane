# Agent Deployment Control Plane

Prompt + model + tool manifest as one versioned, deployable artifact (`AgentSpec`): eval gate in CI, canary on Kubernetes with cost/latency/quality SLOs, multi-objective (Pareto) auto-rollback.

![cost vs quality](results/cost_vs_quality.png)

<!-- results:start -->
| Config | Eval score | Success | Cost/task | p95 | Cache hit |
|---|---:|---:|---:|---:|---:|
| v1: Sonnet | 0.924 | 0.88 | $0.00384 | 5.4 s | 0.92 |
| v2-bad | 0.801 | 0.66 | $0.01932 | 7.1 s | 0.00 |
| v3: Haiku | 0.875 | 0.78 | $0.00018 | 2.8 s | 0.92 |
| GPT-5 | 0.906 | 0.84 | $0.00364 | 7.9 s | 0.92 |
| Gemini 2.5 Pro | 0.877 | 0.80 | $0.00444 | 6.1 s | 0.92 |
| Llama 3.1 8B (vLLM) | 0.835 | 0.64 | $0.00006 | 3.1 s | 0.92 |
| Qwen 2.5 7B (SGLang) | 0.790 | 0.58 | $0.00006 | 2.9 s | 0.92 |
| v1, caching off | 0.924 | 0.88 | $0.01285 | 5.5 s | 0.00 |
| v1, retrieved context | 0.870 | 0.82 | $0.00377 | 5.4 s | 0.80 |

| Rollout experiment | Result |
|---|---|
| Time to detect v2-bad, first canary step 10% / 25% | 97 s / 48 s |
| False rollbacks over 13 candidates: Pareto / thresholds + noise band / fixed thresholds | 0 / 1 / 11 |
| Chaos time to detect: degraded prompt / +1500 ms latency / killed tool | 134 s / 97 s / 44 s-70 s |

Mock provider; numbers are synthetic. Full table (69 rows): [results/results.md](results/results.md).
<!-- results:end -->

| Layer | What | Tech |
|---|---|---|
| Artifact + registry | versioned prompt+model+tools | Pydantic `AgentSpec`, MLflow Model Registry (Postgres, MinIO) |
| Runtime | agent loop, /run API | LangGraph, FastAPI, Redis budgets, Postgres run store |
| Providers | one adapter interface | Anthropic, OpenAI, Gemini, AWS Bedrock, Azure OpenAI / AI Foundry, vLLM, SGLang, mock |
| Tools | tool calls over MCP | MCP server (tau-bench-style airline/retail) |
| Open-model serving | self-hosted Llama / Qwen | vLLM on Ray Serve, KServe, SGLang; prefix caching, fp8 KV cache |
| Events | run, eval, rollback events | Kafka API (Redpanda) |
| Feature flags | gate tools per rollout step | OpenFeature + flagd |
| Observability | traces, metrics, logs | OpenTelemetry (GenAI semconv), OTel Collector, Prometheus, Grafana, Tempo, Loki, Langfuse |
| SLOs | cost, latency, quality budgets | `infra/slo/slo.yaml` -> burn-rate alerts, error budgets |
| CI | lint, tests, eval gate, release | GitHub Actions, Inspect AI, promptfoo, uv |
| Delivery | GitOps canary rollout | Argo CD, Argo Rollouts (canary, blue-green), Helm, Kustomize |
| Traffic | weighted canary split | Istio VirtualService / DestinationRule |
| Rollback | multi-objective decision | Pareto controller -> Argo abort / promote / GitHub issue |
| Chaos | fault injection, MTTD/MTTR | LitmusChaos, in-process fault injector |
| FinOps | cost per task and cluster | Token price table, AKS vs EKS cost, Infracost |
| Cloud | IaC, plan only | Terraform: EKS + IRSA + ECR, AKS + Entra workload identity + ACR |
| Local | full stack on one machine | kind, docker compose, `make demo-local` |

## How I chose

- `AgentSpec` joint artifact over separate prompt CMS + deploy config: rollback restores a known-good prompt x model x tools combination. [ADR-001](docs/ADR-001-agentspec-artifact.md)
- MLflow registry aliases over legacy stages / Git tags: aliases are the supported API; run lineage for evals. [ADR-002](docs/ADR-002-mlflow-registry-aliases.md)
- Fixture-replay mock provider over canned responses / VCR cassettes: exercises the real loop, context and cache math; not tied to one SDK version. [ADR-003](docs/ADR-003-mock-provider-fixtures.md)
- LangGraph over a hand-rolled loop / Deep Agents as default: nodes map 1:1 to spans and budget checks. [ADR-004](docs/ADR-004-langgraph-loop.md)
- MCP over provider-native function calling on in-process functions: tool versioning decoupled from the runtime image. [ADR-005](docs/ADR-005-mcp-tools.md)
- Pareto rule over per-metric thresholds: scale-free; never auto-rejects a deliberate trade-off. [ADR-006](docs/ADR-006-pareto-rollback.md)
- Argo Rollouts + Istio over Flagger / replica ratios: abort/promote on the CR status; exact weights independent of replica count. [ADR-007](docs/ADR-007-argo-rollouts-istio.md)
- Argo CD over Flux / `helm upgrade` from CI: same family as Rollouts; CI holds no cluster credentials. [ADR-008](docs/ADR-008-argocd-gitops.md)
- OpenTelemetry GenAI semconv over provider SDK callbacks: one instrumentation path, model-agnostic fields. [ADR-009](docs/ADR-009-otel-genai.md)
- Langfuse via Collector OTLP over LangSmith / Langfuse SDK: self-hosted, no extra client code, same trace IDs as Tempo. [ADR-010](docs/ADR-010-langfuse-otel.md)
- Own Sloth-format rule generator over the Sloth binary: no extra binary in CI; `--check` fails on drift. [ADR-011](docs/ADR-011-slo-generator.md)
- Redpanda over Apache Kafka / Redis streams: single binary locally, wire-compatible with MSK / Event Hubs. [ADR-012](docs/ADR-012-redpanda.md)
- OpenFeature + flagd over LaunchDarkly / env vars: vendor-neutral, flags in Git. [ADR-013](docs/ADR-013-openfeature-flagd.md)
- Terraform `mock_provider` plans over plan with fake credentials: azurerm authenticates at plan time. [ADR-014](docs/ADR-014-terraform-mock-plan.md)
- Inspect AI + promptfoo over one framework / LLM-as-judge only: deterministic state-match scoring plus fast prompt assertions. [ADR-015](docs/ADR-015-eval-layers.md)
- Static price tables + optional Infracost over billing exports: works offline and in CI. [ADR-016](docs/ADR-016-finops-static-prices.md)
- Ray Serve + KServe + SGLang over KServe only / TGI: Python routing plus platform-standard CRD; both expose vLLM cache metrics. [ADR-017](docs/ADR-017-serving-ray-kserve.md)
- kind + compose over minikube / k3d: upstream conformance tool; stateful services restart faster outside the cluster. [ADR-018](docs/ADR-018-kind-local.md)
- LitmusChaos + in-process injector over Chaos Mesh: Prometheus probes; MTTD/MTTR measurable without a cluster. [ADR-019](docs/ADR-019-chaos.md)
- Release artifact + opt-in GitOps push over bot commits to main: human-authored history, no push loops. [ADR-020](docs/ADR-020-release-no-bot-push.md)
- uv over Poetry / pip-tools: faster resolves, one lockfile. [ADR-021](docs/ADR-021-uv.md)

## Experiment

Frozen set: `evals/frozen/v1` (50 airline/retail tasks, hash in `MANIFEST.json`). Scoring: final DB state vs ground-truth replay + required output substrings.

Measured per arm: task success, eval score, cost/task, p50/p95 latency, tool error rate, tokens in/out, cache hit rate, rollback triggered, time to detect.

| Axis | Variants | Run |
|---|---|---|
| Model | Sonnet, Haiku, GPT-5, Gemini, Llama (vLLM), Qwen (SGLang) | `make matrix` |
| Prompt caching | on / off | `make matrix` |
| Context | full / summarized / retrieved | `make matrix` |
| Canary first step | 10% vs 25% | `make rollout` |
| Rollback rule | Pareto vs thresholds | `make rollout` |
| Chaos | fault injection, MTTD/MTTR | degraded prompt, latency, killed tool | `make chaos` |
| Gate | v1 / v2-bad / v3 | `make eval-gate`, `make promptfoo` |
| End to end | v1 -> v2-bad rollback -> v3 needs-human | `make demo-local` |
| FinOps | cost per task and cluster | per deploy, per 1k tasks, AKS vs EKS | `make finops` |

All rows land in `runs/*.jsonl`; `make report` rebuilds `results/results.md` and `results/cost_vs_quality.png`.

## Results

- v2-bad vs v1: cost/task 5.0x ($0.01932 vs $0.00384), p95 +31.5% (7102 vs 5400 ms), eval -0.123 (0.801 vs 0.924), cache hit 0.00 vs 0.92. Dominated on all three objectives.
- v2-bad clears the offline gate (`evals/gate.yaml`: eval >= 0.75, success >= 0.60, cost <= $0.05, p95 <= 15000 ms); only the canary comparison against stable catches it.
- v3-tradeoff vs v1: cost/task -95.3% ($0.00018), p95 -48.6% (2778 ms), eval -0.049 (0.875). Trade-off, not dominated.
- Models, eval score: Sonnet 0.924, GPT-5 0.906, Gemini 0.877, Haiku 0.875, Llama 0.835, Qwen 0.790.
- Models, cost/task: Llama = Qwen $0.00006, Haiku $0.00018, GPT-5 $0.00364, Sonnet $0.00384, Gemini $0.00444. Highest p95: GPT-5 7869 ms.
- Gemini 2.5 Pro is Pareto-dominated by Sonnet (cost, p95, eval). Other 5 models are on the front.
- Prompt caching off: cost/task 3.35x ($0.01285 vs $0.00384), p95 +126 ms, eval unchanged.
- Context retrieved vs full: eval -0.054 (0.870), success 0.82 vs 0.88, cost/task -2.3%, cache hit 0.80. Summarized: equal eval, cost -0.5%.

- Canary first step 10% vs 25% (v2-bad, 5 seeds, 2 rps simulated): time to detect 97 s vs 48 s; rolled back 5/5 at both.
- False rollbacks over 13 candidates (v2-bad, v3-tradeoff, v1 copy, 10 v1-vs-v1 reruns): Pareto 0, per-metric thresholds with the same noise band 1 (v3-tradeoff), fixed thresholds 11. Missed rollbacks: 0 for all rules.
- Chaos (fault on canary, 5 seeds, MTTD / MTTR): degraded prompt 134 / 139 s, +1500 ms latency 97 / 102 s, killed get_reservation 70 / 75 s, killed get_order 44 / 49 s. MTTR = MTTD + 5 s simulated abort.
- Demo: v2-bad rolled back at 10% after 105 s; v3-tradeoff marked needs-human at 25%, issue written to `runs/issues/`.

<!-- findings -->

## Disclosed

- Model outputs: all replayed synthetic fixtures (`src/adcp/providers/fixtures/mock/`); `MODEL_PROVIDER=mock` everywhere. Latency is simulated. Numbers are not provider benchmarks.
- Token counts come from the real request shape; cost = tokens x `src/adcp/finops/prices.yaml`. Cache hits are simulated by prefix hash.
- Rollout TTD / MTTD / MTTR are simulated seconds from `adcp.rollout.sim` (driving the real harness and controller), not wall-clock cluster time.
- Keys needed for real runs: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GOOGLE_API_KEY`, `AWS_REGION` + AWS credentials, `AZURE_OPENAI_ENDPOINT` / `AZURE_AI_ENDPOINT` + Entra identity (`AZURE_CLIENT_ID`, `AZURE_TENANT_ID`), `VLLM_BASE_URL`, `SGLANG_BASE_URL`, `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_AUTH`, `GITHUB_TOKEN`. See [docs/SETUP.md](docs/SETUP.md).
- Cloud: nothing applied. Terraform is `validate` + `terraform test` (plan against mock providers) only. No cluster or containers were started in the build environment; images were not built or pushed. K8s manifests checked with `helm lint`, `kustomize build`, kubeconform.
- Helm chart versions in `infra/deploy/argocd/apps/*.yaml` and `scripts/up.sh` (Argo CD 7.7.11, Argo Rollouts 2.38.0, Istio 1.30.7, kube-prometheus-stack 66.3.1, Tempo 1.14.0, Loki 6.24.0, Langfuse 1.2.14, KServe v0.14.1, OpenFeature Operator v0.8.5) were pinned from memory where chart repos were unreachable.

Architecture: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Setup and keys: [docs/SETUP.md](docs/SETUP.md). Keyword map: [docs/KEYWORD_MAP.md](docs/KEYWORD_MAP.md). Status and gaps: [docs/STATUS.md](docs/STATUS.md).

## Run

```bash
make install
```

```bash
make demo-local
```

```bash
make test
```

```bash
make eval-gate
```

```bash
make up && make demo
```

```bash
make plan-aws
```

```bash
make plan-azure
```
