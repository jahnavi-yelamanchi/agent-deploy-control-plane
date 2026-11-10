#!/usr/bin/env bash
# Stage 1 of the full flow, no Kubernetes: data plane + observability + runtime, evaluator and
# rollback controller in docker compose, then one real request through the runtime.
set -euo pipefail
cd "$(dirname "$0")/.."

command -v docker >/dev/null || { echo "missing: docker" >&2; exit 1; }
docker info >/dev/null 2>&1 || { echo "Docker is not running. Start Docker Desktop and retry." >&2; exit 1; }
ENVF=(); [[ -f .env ]] && ENVF=(--env-file .env)
COMPOSE=(docker compose "${ENVF[@]}" -f infra/compose/docker-compose.yaml)

"${COMPOSE[@]}" up -d --build --wait

echo "== smoke: one task through the live runtime"
curl -fsS localhost:8080/healthz && echo
curl -fsS -X POST localhost:8080/run -H 'content-type: application/json' \
  -d '{"task_id":"airline-001","input":""}' |
  python3 -c "import json,sys; r=json.load(sys.stdin); print({k: r[k] for k in ('status','provider','model_id','cost_usd','latency_ms','steps')}); print(r['output'][:200])"

cat <<MSG

up-lite: running
  Runtime     http://localhost:8080/docs      POST /run
  Rollback    http://localhost:8090/docs      POST /decide
  Grafana     http://localhost:3000           dashboards: Agent overview, GenAI traces
  Langfuse    http://localhost:3001           admin@adcp.local / adcp-local-admin
  MLflow      http://localhost:5000           AgentSpec registry
  Prometheus  http://localhost:9090
  Stop:       make down
MSG
