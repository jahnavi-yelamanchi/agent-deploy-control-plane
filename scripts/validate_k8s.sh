#!/usr/bin/env bash
# helm lint + helm template | kubeconform, kustomize build | kubeconform, raw manifests | kubeconform.
# CRD schemas from datreeio/CRDs-catalog; kinds without a published schema are skipped.
# OFFLINE=1 (or unreachable schema host) degrades to helm lint + kustomize build + YAML parse.
set -euo pipefail
cd "$(dirname "$0")/.."

KUBECONFORM="${KUBECONFORM:-$(command -v kubeconform || echo "$HOME/go/bin/kubeconform")}"
CRD_SCHEMAS='https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json'
FLAGS=flags/flags.flagd.json
fail=0

log() { printf '\n== %s\n' "$*"; }

schemas_reachable() {
  [[ "${OFFLINE:-0}" != 1 ]] && [[ -x "$KUBECONFORM" ]] &&
    printf 'apiVersion: v1\nkind: ConfigMap\nmetadata: {name: probe}\n' |
      timeout 30 "$KUBECONFORM" -strict - >/dev/null 2>&1
}

if schemas_reachable; then
  check() { "$KUBECONFORM" -strict -summary -ignore-missing-schemas \
              -schema-location default -schema-location "$CRD_SCHEMAS" "$@"; }
else
  echo "kubeconform schemas unavailable: YAML parse only"
  check() { python3 -c 'import sys,yaml; n=sum(1 for d in yaml.safe_load_all(sys.stdin) if d); print(f"parsed {n} docs")'; }
fi

run() {
  if ! "$@"; then echo "FAIL: $*"; fail=1; fi
}

set_file=()
[[ -f "$FLAGS" ]] && set_file=(--set-file "flagd.flagsJson=$FLAGS")

log "helm: agent-runtime"
chart=deploy/helm/agent-runtime
run helm lint "$chart" "${set_file[@]}"
for args in \
  "" \
  "--set strategy=blueGreen" \
  "--set serviceAccount.cloud=aws --set serviceAccount.awsRoleArn=arn:aws:iam::123456789012:role/x" \
  "--set serviceAccount.cloud=azure --set serviceAccount.azureClientId=00000000-0000-0000-0000-000000000000 --set autoscaling.enabled=true --set flagd.mode=remote"; do
  echo "-- values: ${args:-defaults}"
  # shellcheck disable=SC2086
  helm template agent-runtime "$chart" -n agents "${set_file[@]}" $args | check || fail=1
done

log "helm: rollback-controller"
run helm lint deploy/helm/rollback-controller
helm template rollback-controller deploy/helm/rollback-controller -n agents | check || fail=1

for o in deploy/kustomize/overlays/*/; do
  log "kustomize: $o"
  kustomize build "$o" | check || fail=1
done

for k in deploy/argocd deploy/istio chaos/litmus; do
  log "kustomize: $k"
  kustomize build "$k" | check || fail=1
done

log "raw: deploy/serving deploy/kind"
for f in deploy/serving/*/*.yaml deploy/kind/*.yaml; do
  case "$f" in
    deploy/kind/*) python3 -c 'import sys,yaml; yaml.safe_load(open(sys.argv[1])); print("ok", sys.argv[1])' "$f" || fail=1 ;;
    *) check < "$f" || fail=1 ;;
  esac
done

if [[ $fail -ne 0 ]]; then echo; echo "validate_k8s: FAILED"; exit 1; fi
echo; echo "validate_k8s: OK"
