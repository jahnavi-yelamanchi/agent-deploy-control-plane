#!/usr/bin/env bash
# Infracost breakdown for EKS + AKS terraform; static FinOps table when infracost is absent.
set -euo pipefail
cd "$(dirname "$0")/.."

if command -v infracost >/dev/null 2>&1; then
  if [[ -z "${INFRACOST_API_KEY:-}" ]] && ! infracost configure get api_key >/dev/null 2>&1; then
    echo "infracost: no API key (infracost auth login or INFRACOST_API_KEY); falling back" >&2
  else
    exec infracost breakdown --config-file infra/infracost.yml --show-skipped "$@"
  fi
else
  echo "infracost not on PATH (https://www.infracost.io/docs/); printing static table from adcp.finops.report" >&2
fi
exec uv run python -m adcp.finops.report "$@"
