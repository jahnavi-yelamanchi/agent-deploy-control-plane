#!/usr/bin/env bash
# Delete the kind cluster and the compose stack. KEEP_VOLUMES=1 keeps compose data.
set -euo pipefail
cd "$(dirname "$0")/.."
CLUSTER=${CLUSTER:-adcp}

if command -v kind >/dev/null && kind get clusters 2>/dev/null | grep -qx "$CLUSTER"; then
  kind delete cluster --name "$CLUSTER"
fi
if [[ "${KEEP_VOLUMES:-0}" == 1 ]]; then
  docker compose -f infra/compose/docker-compose.yaml --profile gpu --profile console down --remove-orphans
else
  docker compose -f infra/compose/docker-compose.yaml --profile gpu --profile console down -v --remove-orphans
fi
echo "down: done"
