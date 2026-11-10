# Status

## Next steps (resume here)

1. **Unlock GitHub Actions.** Since Nov 9 every run fails in ~3 s: "The job was not started because your account is locked due to a billing issue." Fix at github.com/settings/billing (failed card or unpaid invoice; the repo is public, so minutes are free). Until then CI shows red.
2. **Add the repo secret `ANTHROPIC_API_KEY`** (Settings > Secrets and variables > Actions).
3. **Run the full flow online** with `.github/workflows/e2e.yml` (Actions > e2e > Run workflow), in this order: `lite/mock`, `full/mock`, `lite/real`, `full/real`. Never executed yet: `scripts/up-lite.sh`, `scripts/up.sh`, `scripts/demo.sh`, the compose stack and the Helm charts on a live cluster. Expect fixes; read the "Diagnostics" step on failure. The laptop has no free disk, so do not run Docker locally.
4. **Explain the real tool error rate** (12.6% on Claude Haiku vs 1.5% mock). The runner keeps only totals: make it write per-task tool calls and errors, rerun Haiku (~3 cents), fix the cause.
5. **Real run of the main spec** (`specs/v1.yaml`, Sonnet, `--context full`, ~$3-5) into `runs/real.jsonl` with `--experiment real --variant sonnet`; then `make report` and update the Results section of README.md (it now shows one real Haiku run; rollout and chaos numbers there are mock-based).
6. **Optional:** `GITHUB_TOKEN` (fine-grained, this repo, Issues read/write) in `.env` for needs-human issues outside CI; CI uses the built-in token.
7. **Launch notes:** pick a short project name; record a 20 s clip of the demo catching a bad version; first posts name the project, show one thing it does, link the repo.

## Works end to end locally (run in the build environment, MODEL_PROVIDER=mock)

| Target | Result |
|---|---|
| `make demo-local` | register v1 -> production; v2-bad gate pass -> canary -> Pareto rollback at 10% (TTD 105 s simulated) -> registry `rolled_back`, Argo abort (dry run); v3-tradeoff -> needs-human at 25% -> issue file in `runs/issues/`. 18 s wall. |
| `make test` | 22 tests (one smoke per package + e2e), 22 s |
| `make lint`, `make typecheck` | ruff clean, mypy clean (60 files), promtool 3.1.0 `check rules` (88 rules) + `test rules` pass |
| `make eval-gate` | Inspect AI over 50 frozen tasks with `mockllm/model`; v1, v2-bad, v3 pass offline thresholds |
| `make promptfoo` | 10/10 cases on v1 (promptfoo 0.103.5, `--no-write`) |
| `make matrix rollout chaos report finops` | `runs/*.jsonl`, `results/results.md`, `results/summary.md`, `results/cost_vs_quality.png`, `results/finops.md` |
| `make plan-aws`, `make plan-azure` | `init -backend=false`, `validate`, `terraform test` (mock providers, `command = plan`) pass |
| `make validate-k8s` | helm lint, helm template x4 value sets, kubeconform -strict with upstream + CRD catalog schemas, 7 kustomize builds |
| `docker compose -f infra/compose/docker-compose.yaml config -q` | passes (daemon not required) |
| GitHub Actions | `ci` (python, terraform x2, kubernetes, prometheus), `eval-gate` (Inspect AI, promptfoo), `release` (gate, MLflow register, image build, values bump artifact) green on push to main. `nightly-eval` not yet triggered (schedule). |

| Live services (no Docker) | `uvicorn adcp.runtime.app:app` answers `POST /run` (airline-001: verify -> get -> cancel, ok); `python -m adcp.rollback.service` answers `POST /decide` (v2-like canary -> rollback); empty `/decide` without Prometheus -> 503, no action |
| Real adapters | Anthropic (SDK 2.3) and OpenAI (SDK 3.42) adapters exercised through the real SDKs with a faked HTTP reply (`tests/test_providers_smoke.py`); Claude thinking blocks resent unchanged |

## Not executed here

- `make up`, `make demo`: no Docker daemon, no kind cluster. Compose, kind, Argo CD, Argo Rollouts, Istio, LitmusChaos, Ray Serve, KServe, SGLang assets written and validated statically only.
- Docker image not built locally; built successfully in `release.yml` on GitHub Actions, not pushed (`PUSH_IMAGE` unset).
- OTel Collector, Tempo, Loki configs YAML-parsed only (otelcol-contrib download blocked).
- RayService and Litmus kinds have no kubeconform schema; skipped, not validated.
- `terraform apply` never run. No backend block; add one before applying.
- Infracost not installed; FinOps uses the static price table in `src/adcp/finops/infra_prices.yaml`.
- `scripts/up.sh`, `scripts/demo.sh`: `bash -n` only (no shellcheck).
- Mermaid diagrams not rendered locally.

## Needs keys

`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GOOGLE_API_KEY`, `AWS_REGION` + keys/profile/IRSA role, `AZURE_OPENAI_ENDPOINT` / `AZURE_AI_ENDPOINT` + Entra identity or `AZURE_OPENAI_API_KEY`, `VLLM_BASE_URL`, `SGLANG_BASE_URL`, `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_AUTH`, `GITHUB_TOKEN` (needs-human issues). See `docs/SETUP.md`; `make bootstrap` flips mock -> real.

## Known gaps

- All model outputs are replayed synthetic fixtures; latency is simulated. Numbers compare configurations, not providers.
- One real model run so far: Claude Haiku 5.5 on all 50 tasks from a laptop (`runs/real.jsonl`). Sonnet, OpenAI, Gemini and open models still mock-only. Real tool error rate 0.126 not yet investigated (per-task logs not kept by the runner). `deep_agent.py` (LangChain Deep Agents) not exercised; `deep` extra not installed.
- Helm chart versions for argo-cd 7.7.11, argo-rollouts 2.38.0, kube-prometheus-stack 66.3.1, tempo 1.14.0, loki 6.24.0, langfuse 1.2.14, open-feature-operator v0.8.5, kserve v0.14.1 pinned from memory (repos returned 403 via proxy); Langfuse chart values keys unverified. Istio 1.30.7 verified.
- Container tags pinned, not pulled; `langfuse:3` is a major tag. MLflow container pip-installs psycopg2/boto3 at start.
- In cluster the rollback controller reads Prometheus only; Prometheus gives no standard errors, so decisions use fixed tolerances (in-process sim uses z=3 noise band). `agent.evals` is consumed in-process only.
- AnalysisTemplates filter on `track`/`agent_version` labels; with no canary samples, eval/latency/cost checks fail closed.
- Cost SLO counts dollars over $0.02 x runs, not runs over budget. Quality SLO at 90% cannot fire the 14.4x page pair; 6x pair still pages.
- promptfoo 0.103.5 crashes writing results (`SqliteError: FOREIGN KEY`); runs with `--no-write`. v3 prompt fails 2 cases, left commented out.
- MCP http mode serves one shared env; per-task env only in in-process and stdio modes.
- e2e test 13 s (over the 10 s smoke target).

## Pushes

Last successful push: see `git log origin/main -1`. All pushes in this session succeeded on first attempt.
