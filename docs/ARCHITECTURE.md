# Architecture

## Rollout and rollback loop

```mermaid
flowchart LR
  commit["commit: specs/ src/ evals/ flags/"] --> gate["GitHub Actions eval-gate<br/>Inspect AI suite + promptfoo<br/>frozen set evals/frozen/v1"]
  gate --> release["release.yml<br/>gate (blocking) + image build"]
  release --> reg[("MLflow Model Registry<br/>AgentSpec version, alias candidate")]
  release --> bump["GitOps values bump<br/>image.tag, agentSpec.version"]
  bump --> argocd["Argo CD sync<br/>app-of-apps"]
  argocd --> rollout["Argo Rollouts canary<br/>10 / 25 / 50 / 100"]
  rollout --> vs["Istio VirtualService<br/>route primary: stable / canary weights"]
  vs --> stable["agent-runtime-stable"]
  vs --> canary["agent-runtime-canary"]

  subgraph runtime["runtime pods"]
    stable
    canary
  end

  runtime -- "OTLP: gen_ai.* + adcp_*" --> otel["OTel Collector"]
  otel --> prom[("Prometheus<br/>adcp:* recording rules")]
  otel --> tempo[("Tempo")]
  otel --> loki[("Loki")]
  otel --> langfuse[("Langfuse")]

  runtime -- "agent.runs" --> kafka[["Kafka / Redpanda"]]
  kafka --> evaluator["online evaluator<br/>adcp.evals.online"]
  evaluator -- "agent.evals" --> kafka
  evaluator -- "adcp_eval_score" --> otel

  prom --> ctrl["rollback controller<br/>adcp.rollback.service"]
  kafka -. "agent.evals<br/>(InMemorySource: demo / sim)" .-> ctrl
  prom --> analysis["AnalysisTemplates<br/>static thresholds"]
  analysis --> rollout

  ctrl --> pareto{"Pareto decision<br/>cost/task, p95, eval score<br/>+ hard SLO guard"}
  pareto -- rollback --> abort["Argo Rollouts abort<br/>registry tag rolled_back"]
  pareto -- promote --> promote["Argo Rollouts promote / promoteFull<br/>registry alias production"]
  pareto -- needs_human --> issue["GitHub issue<br/>rollout stays paused"]
  pareto -- hold --> ctrl
  abort --> rollout
  promote --> rollout
  ctrl -- "agent.rollbacks" --> kafka
```

## One canary analysis step

```mermaid
sequenceDiagram
  participant AR as Argo Rollouts
  participant IS as Istio VirtualService
  participant RT as agent-runtime (stable / canary)
  participant EV as online evaluator
  participant PR as Prometheus
  participant RC as rollback controller
  participant RG as MLflow registry
  participant GH as GitHub

  AR->>IS: setWeight 25 (route primary)
  AR->>AR: pause 2m
  loop requests
    IS->>RT: /run (track label from pod, adcp.dev/track)
    RT->>PR: adcp_* + gen_ai.* via OTel Collector
    RT->>EV: agent.runs (Kafka)
    EV->>PR: adcp_eval_score{track, agent_version}
  end
  RC->>AR: GET rollout status (revision, step, weight)
  RC->>PR: query adcp:cost_per_task, adcp:latency_p95, adcp:eval_score, adcp:error_ratio, adcp:tool_error_ratio
  RC->>RC: compare(stable, canary, ParetoPolicy)
  alt SLO breach or canary dominated
    RC->>AR: PATCH status.abort=true
    RC->>RG: set_stage(canary version, rolled_back)
  else canary dominates or equal
    RC->>AR: PATCH clear pauseConditions (final step: promoteFull)
    RC->>RG: set_stage(canary version, production) on final step
  else trade-off
    RC->>GH: create issue (labels rollout, needs-human) with comparison table
  else either arm n < min_samples
    RC->>RC: hold
  end
  RC->>PR: adcp_rollback_events_total{decision, reason}
```

## Decision rule (`src/adcp/rollback/pareto.py`)

| Order | Condition | Decision |
|---|---|---|
| 1 | Canary n >= 10 and error ratio > 0.05, p95 > 15000 ms, or tool error rate > stable + 0.15 | rollback (`slo_breach`) |
| 2 | Either arm n < 20 | hold |
| 3 | No objective better, at least one worse | rollback (`dominated`) |
| 4 | No objective worse | promote (`dominates` / `equal`) |
| 5 | At least one better and one worse | needs_human (`tradeoff`) |

Per-objective `equal` band: max(tolerance, 3 x combined standard error). Tolerances: cost 3% rel, p95 5% rel, eval score 0.02 abs. Prometheus source has no standard errors, so it uses the fixed tolerances. `naive` rule (baseline): rollback if any objective is worse beyond its tolerance.

## Components

| Module / path | Responsibility |
|---|---|
| `src/adcp/spec.py` | `AgentSpec`, `Stage`, `Provider`, fingerprint, YAML IO |
| `src/adcp/types.py` | `RunResult`, `EvalResult`, `ArmMetrics`, `Usage` |
| `src/adcp/registry.py` | `SpecRegistry` on MLflow: register, aliases, `rolled_back` tag, history |
| `src/adcp/cli.py` | `adcp run / register / promote / history / serve / mcp` |
| `src/adcp/providers/` | Adapters (Anthropic, OpenAI, Gemini, Bedrock, Azure, vLLM, SGLang), mock fixture replay, `get_provider` |
| `src/adcp/runtime/agent.py` | LangGraph loop, context strategy, cost/latency accounting, budget |
| `src/adcp/runtime/app.py` | FastAPI `/run /healthz /readyz /spec`; spec from file or registry alias; track resolution |
| `src/adcp/runtime/harness.py` | `run_task` / `arun_task`; fault hooks |
| `src/adcp/runtime/mcp_server.py`, `mcp_client.py` | MCP server `tau`; in-process / stdio / HTTP client |
| `src/adcp/runtime/flags.py` | OpenFeature + flagd (RPC, in-process, in-memory fallback) |
| `src/adcp/runtime/budget.py`, `store.py` | Redis / local budgets and rate limits; Postgres / SQLite run store |
| `src/adcp/runtime/events.py` | Kafka (aiokafka) or in-process bus; topics |
| `src/adcp/runtime/telemetry.py` | OTel traces + metrics, GenAI semconv, `adcp_*` |
| `src/adcp/runtime/deep_agent.py` | Optional LangChain Deep Agents builder (reference) |
| `src/adcp/env/` | tau-bench-style airline/retail env, tasks, state-based scoring |
| `src/adcp/evals/runner.py`, `gate.py`, `freeze.py` | Offline eval, CI gate vs `evals/gate.yaml`, frozen-set hash |
| `src/adcp/evals/inspect_suite.py`, `inspect_runner.py`; `evals/inspect/`, `evals/promptfoo/` | Inspect AI suite; promptfoo config + Python provider |
| `src/adcp/evals/matrix.py`, `metrics.py` | Comparison matrix; `runs/*.jsonl` row schema |
| `src/adcp/evals/online.py` | `agent.runs` -> score -> `agent.evals` + `adcp_eval_score` |
| `src/adcp/rollback/pareto.py` | Pareto / naive rules, SLO guard |
| `src/adcp/rollback/controller.py` | Decide -> act (Argo, registry, GitHub), dedupe per revision/step, publish `agent.rollbacks` |
| `src/adcp/rollback/metrics_source.py` | `PrometheusSource` (recording rules), `InMemorySource` (runs/evals, standard errors) |
| `src/adcp/rollback/argo.py`, `github.py`, `service.py` | Rollout status patches; issue client (dry run writes `runs/issues/*.md`); FastAPI :8090 |
| `src/adcp/rollout/sim.py`, `experiments.py` | Simulated canary / blue-green over the real harness; canary weight + rule experiments |
| `src/adcp/chaos/faults.py`, `scripts/chaos_suite.py`, `chaos/litmus/` | Degrade prompt, add latency, kill tool; MTTD/MTTR; Litmus pod-delete, network latency |
| `src/adcp/demo.py` | `make demo-local`: v1 -> production, v2-bad -> rollback, v3 -> needs_human |
| `src/adcp/finops/` | Token cost, price tables, AKS vs EKS report |
| `deploy/helm/agent-runtime/` | Rollout (canary / blueGreen), AnalysisTemplates, VirtualService, DestinationRule, services, HPA |
| `deploy/helm/rollback-controller/` | Controller + online evaluator deployments, RBAC for rollouts patch |
| `deploy/argocd/`, `deploy/kustomize/` | App-of-apps; local / aws / azure overlays |
| `deploy/istio/`, `deploy/serving/` | Gateway, mTLS, telemetry; Ray Serve vLLM, KServe, SGLang |
| `observability/`, `slo/`, `scripts/gen_slo_rules.py` | Collector, Prometheus rules, Grafana dashboards, Tempo, Loki; SLO -> burn-rate rules |
| `terraform/aws/`, `terraform/azure/` | EKS + IRSA + ECR + S3; AKS + workload identity + ACR + Azure OpenAI + AI Foundry |
| `compose/`, `deploy/kind/`, `scripts/up.sh`, `scripts/demo.sh` | Local data plane + observability; kind cluster; cluster demo |
| `.github/workflows/` | `ci`, `eval-gate`, `release`, `nightly-eval` |
