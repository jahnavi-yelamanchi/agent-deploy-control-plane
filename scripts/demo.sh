#!/usr/bin/env bash
# deploy -> eval -> canary -> rollback loop against the kind cluster from `make up`:
#   v1 (baseline) -> stable; v2-bad -> canary, dominated on cost/latency/quality -> rollback;
#   v3-tradeoff -> canary, Pareto trade-off -> needs_human.
# GitOps path is release.yml bumping image.tag/agentSpec.version in git; this demo drives
# helm directly, so it pauses Argo CD auto-sync for agent-runtime first.
set -euo pipefail
cd "$(dirname "$0")/.."

CTX=${CTX:-kind-adcp}
NS=agents
CHART=infra/deploy/helm/agent-runtime
TAG=${TAG:-dev}
TRAFFIC_SECONDS=${TRAFFIC_SECONDS:-240}
export MODEL_PROVIDER=mock MLFLOW_TRACKING_URI=${MLFLOW_TRACKING_URI:-http://localhost:5000}
TMP=$(mktemp -d)
PIDS=()
cleanup() { for p in "${PIDS[@]}"; do kill "$p" 2>/dev/null || true; done; rm -rf "$TMP"; }
trap cleanup EXIT

if ! command -v kubectl >/dev/null || ! kubectl --context "$CTX" get ns "$NS" >/dev/null 2>&1; then
  echo "demo: no cluster at context $CTX (run 'make up' first). Running the in-process loop instead: make demo-local"
  exec make demo-local
fi
kubectl argo rollouts version >/dev/null 2>&1 || { echo "demo: install the kubectl-argo-rollouts plugin" >&2; exit 1; }
k() { kubectl --context "$CTX" "$@"; }

step() { printf '\n### %s\n' "$*"; }

# AgentSpec yaml -> helm values file (agentSpec.version + agentSpec.spec)
values_for() {
  uv run python - "$1" "$TMP/$(basename "$1").values.yaml" <<'PY'
import sys, yaml
spec = yaml.safe_load(open(sys.argv[1]))
yaml.safe_dump({"agentSpec": {"name": spec["name"], "version": str(spec["version"]), "spec": spec}},
               open(sys.argv[2], "w"), sort_keys=False)
print(sys.argv[2])
PY
}

register() {
  uv run python - "$1" <<'PY'
import sys
from adcp.registry import SpecRegistry
from adcp.spec import AgentSpec, Stage
reg = SpecRegistry()
v = reg.register(AgentSpec.from_yaml(sys.argv[1]))
reg.set_stage(v, Stage.candidate)
print(f"registered {sys.argv[1]} as version {v}")
PY
}

deploy() {
  local spec=$1 values
  values=$(values_for "$spec")
  helm --kube-context "$CTX" upgrade --install agent-runtime "$CHART" -n "$NS" \
    --set image.tag="$TAG" --set-file flagd.flagsJson=flags/flags.flagd.json -f "$values" \
    -f <(printf 'env:\n  MLFLOW_TRACKING_URI: http://mlflow:5000\n  REDIS_URL: redis://redis:6379/0\n  KAFKA_BOOTSTRAP: redpanda:9092\n  POSTGRES_DSN: postgresql://adcp:adcp@postgres:5432/adcp\n')
}

traffic() { # background load through the mesh so Istio splits stable/canary by weight
  uv run python - "$TRAFFIC_SECONDS" <<'PY' &
import json, sys, time, httpx
tasks = [json.loads(l) for l in open("evals/tasks/tasks.jsonl")]
end, i = time.time() + float(sys.argv[1]), 0
with httpx.Client(base_url="http://localhost:18080", timeout=30) as c:
    while time.time() < end:
        t = tasks[i % len(tasks)]; i += 1
        try:
            c.post("/run", json={"input": t["user_input"], "task_id": t["id"]})
        except httpx.HTTPError:
            time.sleep(1)
PY
  PIDS+=($!)
}

decide() {
  k -n "$NS" port-forward svc/rollback-controller 18090:8090 >/dev/null 2>&1 &
  PIDS+=($!); sleep 2
  curl -fsS -X POST localhost:18090/decide | uv run python -m json.tool
}

watch_rollout() { k argo rollouts get rollout agent-runtime -n "$NS" --watch & PIDS+=($!); }

step "pause Argo CD auto-sync for agent-runtime (demo drives helm directly)"
k -n argocd patch application agent-runtime --type merge -p '{"spec":{"syncPolicy":{"automated":null}}}' || true

step "register specs in MLflow Model Registry"
for s in specs/v1.yaml specs/v2-bad.yaml specs/v3-tradeoff.yaml; do register "$s"; done

step "v1 -> stable"
deploy specs/v1.yaml
k argo rollouts status agent-runtime -n "$NS" --timeout 10m

step "v2-bad -> canary (expect rollback: dominated on cost, latency, quality)"
deploy specs/v2-bad.yaml
watch_rollout
traffic
sleep 150
decide
k argo rollouts status agent-runtime -n "$NS" --timeout 2m || true
k argo rollouts get rollout agent-runtime -n "$NS"

step "restore stable (v1) and try v3-tradeoff (expect needs_human: Pareto trade-off)"
deploy specs/v1.yaml
k argo rollouts status agent-runtime -n "$NS" --timeout 10m || true
deploy specs/v3-tradeoff.yaml
traffic
sleep 150
decide
k argo rollouts get rollout agent-runtime -n "$NS"

step "re-enable Argo CD auto-sync"
k -n argocd patch application agent-runtime --type merge \
  -p '{"spec":{"syncPolicy":{"automated":{"prune":true,"selfHeal":true}}}}' || true
