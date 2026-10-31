#!/usr/bin/env bash
# kind cluster + Argo CD + Argo Rollouts + Istio + kube-prometheus-stack + Tempo + Loki,
# compose data plane attached to the kind network, runtime image loaded into kind,
# then the Argo CD app-of-apps (local overlay) takes over.
set -euo pipefail
cd "$(dirname "$0")/.."

CLUSTER=${CLUSTER:-adcp}
IMAGE=${IMAGE:-ghcr.io/jahnavi-yelamanchi/agent-runtime:dev}
CTX="kind-$CLUSTER"
COMPOSE=(docker compose -f compose/docker-compose.yaml)
# Pinned to match deploy/argocd/apps/*.yaml.
ARGOCD_CHART=7.7.11
ROLLOUTS_CHART=2.38.0
ISTIO_CHART=1.30.7
KPS_CHART=66.3.1
TEMPO_CHART=1.14.0
LOKI_CHART=6.24.0

need() { command -v "$1" >/dev/null || { echo "missing: $1" >&2; exit 1; }; }
for b in docker kind kubectl helm; do need "$b"; done
kubectl argo rollouts version >/dev/null 2>&1 || echo "note: kubectl-argo-rollouts plugin not found (needed by demo.sh)"

# 1. cluster
if ! kind get clusters | grep -qx "$CLUSTER"; then
  kind create cluster --name "$CLUSTER" --config deploy/kind/kind-config.yaml
fi
kubectl config use-context "$CTX"

# 2. image
docker build -t "$IMAGE" .
kind load docker-image "$IMAGE" --name "$CLUSTER"

# 3. data plane + observability backends in compose; attach to the kind docker network so
#    pods resolve mlflow, redis, redpanda, postgres, otel-collector by name.
DATA_SVCS=(postgres redis redpanda redpanda-init minio minio-init mlflow flagd
           otel-collector tempo loki prometheus grafana clickhouse langfuse-worker langfuse-web)
"${COMPOSE[@]}" up -d --wait "${DATA_SVCS[@]}" || "${COMPOSE[@]}" up -d "${DATA_SVCS[@]}"
for svc in postgres redis redpanda minio mlflow flagd otel-collector tempo loki; do
  cid=$("${COMPOSE[@]}" ps -q "$svc")
  [[ -n "$cid" ]] && docker network connect --alias "$svc" kind "$cid" 2>/dev/null || true
done

# 4. platform bootstrap via helm (Argo CD adopts these releases on first root sync)
helm repo add argo https://argoproj.github.io/argo-helm >/dev/null
helm repo add istio https://istio-release.storage.googleapis.com/charts >/dev/null
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null
helm repo add grafana https://grafana.github.io/helm-charts >/dev/null
helm repo update >/dev/null

helm upgrade --install argocd argo/argo-cd --version "$ARGOCD_CHART" -n argocd --create-namespace \
  --set server.service.type=NodePort --set server.service.nodePortHttp=30081 \
  --set 'configs.params.server\.insecure=true' --wait
helm upgrade --install istio-base istio/base --version "$ISTIO_CHART" -n istio-system --create-namespace \
  --set defaultRevision=default --wait
helm upgrade --install istiod istio/istiod --version "$ISTIO_CHART" -n istio-system --wait
helm upgrade --install istio-ingressgateway istio/gateway --version "$ISTIO_CHART" -n istio-ingress \
  --create-namespace --set service.type=NodePort --wait -f - <<'VALUES'
service:
  ports:
    - {name: status-port, port: 15021, targetPort: 15021, protocol: TCP}
    - {name: http2, port: 80, targetPort: 80, nodePort: 30080, protocol: TCP}
    - {name: https, port: 443, targetPort: 443, nodePort: 30443, protocol: TCP}
VALUES
helm upgrade --install argo-rollouts argo/argo-rollouts --version "$ROLLOUTS_CHART" -n argo-rollouts \
  --create-namespace --set dashboard.enabled=true --wait
helm upgrade --install kube-prometheus-stack prometheus-community/kube-prometheus-stack \
  --version "$KPS_CHART" -n monitoring --create-namespace \
  --set prometheus.prometheusSpec.enableRemoteWriteReceiver=true \
  --set prometheus.prometheusSpec.serviceMonitorSelectorNilUsesHelmValues=false \
  --set prometheus.prometheusSpec.podMonitorSelectorNilUsesHelmValues=false \
  --set prometheus.service.type=NodePort --set prometheus.service.nodePort=30090 \
  --set grafana.service.type=NodePort --set grafana.service.nodePort=30030 --wait
helm upgrade --install tempo grafana/tempo --version "$TEMPO_CHART" -n monitoring --wait
helm upgrade --install loki grafana/loki --version "$LOKI_CHART" -n monitoring \
  --set deploymentMode=SingleBinary --set loki.auth_enabled=false \
  --set loki.commonConfig.replication_factor=1 --set loki.storage.type=filesystem \
  --set singleBinary.replicas=1 --set read.replicas=0 --set write.replicas=0 --set backend.replicas=0 \
  --set loki.useTestSchema=true --wait

# 5. GitOps: namespaces, AppProject, app-of-apps root (local overlay values)
kubectl apply -k deploy/kustomize/overlays/local
kubectl -n argocd wait --for=jsonpath='{.status.sync.status}'=Synced application/adcp-root --timeout=10m || true

cat <<MSG

up: cluster $CTX ready
  Argo CD      http://localhost:28080   (admin / kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d)
  Ingress      http://localhost:18080   (agent-runtime via istio-ingress/adcp-gateway)
  Prometheus   http://localhost:29090   (cluster)  | http://localhost:9090 (compose)
  Grafana      http://localhost:23000   (cluster)  | http://localhost:3000 (compose)
  MLflow       http://localhost:5000    Langfuse http://localhost:3001
  Rollout      kubectl argo rollouts get rollout agent-runtime -n agents --watch
MSG
