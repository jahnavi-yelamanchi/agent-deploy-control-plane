#!/usr/bin/env bash
# Flip from mock to real providers: check .env keys, install `providers` extra, set MODEL_PROVIDER=real.
# Usage: scripts/bootstrap.sh [--record]   (--record re-records fixtures from real providers)
set -euo pipefail
cd "$(dirname "$0")/.."

[[ -f .env ]] || { cp .env.example .env; echo "created .env from .env.example; fill in keys and re-run"; exit 1; }
set -a; source .env; set +a

have() { [[ -n "${!1:-}" ]]; }
check() { # name, vars...
  local name=$1; shift; local ok=1
  for v in "$@"; do have "$v" || ok=0; done
  printf '%-18s %s\n' "$name" "$([[ $ok == 1 ]] && echo ok || echo "missing: $*")"
  [[ $ok == 1 ]]
}

n=0
check anthropic ANTHROPIC_API_KEY && n=$((n+1)) || true
check openai OPENAI_API_KEY && n=$((n+1)) || true
check google GOOGLE_API_KEY && n=$((n+1)) || true
check aws_bedrock AWS_REGION AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY && n=$((n+1)) || true
if have AZURE_OPENAI_ENDPOINT || have AZURE_AI_ENDPOINT; then
  check azure AZURE_TENANT_ID AZURE_CLIENT_ID && n=$((n+1)) || true
else
  echo "azure              missing: AZURE_OPENAI_ENDPOINT or AZURE_AI_ENDPOINT"
fi
check langfuse LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY || true

if [[ $n == 0 ]]; then
  echo "no provider credentials found; staying on MODEL_PROVIDER=mock"
  exit 1
fi

uv sync --extra dev --extra evals --extra providers

if grep -q '^MODEL_PROVIDER=' .env; then
  sed -i.bak 's/^MODEL_PROVIDER=.*/MODEL_PROVIDER=real/' .env && rm -f .env.bak
else
  echo 'MODEL_PROVIDER=real' >> .env
fi
echo "MODEL_PROVIDER=real set in .env ($n provider(s) configured)"

if [[ "${1:-}" == "--record" ]]; then
  MODEL_PROVIDER=real uv run python scripts/record_fixtures.py
fi
