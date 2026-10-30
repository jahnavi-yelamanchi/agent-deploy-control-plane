# ADR-018: kind for local Kubernetes, compose for data plane

Context: Full stack must run on one laptop.
Decision: kind runs Argo CD, Rollouts, Istio and the runtime; docker compose runs stateful services and observability.
Alternative rejected: minikube; k3d; everything in-cluster.
Why: kind is the upstream conformance tool; stateful services in compose restart faster.
Consequence: Cluster reaches compose services via the host network.
