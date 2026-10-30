# ADR-007: Argo Rollouts with Istio traffic splitting

Context: Need weighted canary 10/25/50/100 with metric analysis and an abort API.
Decision: Argo Rollouts canary with Istio VirtualService/DestinationRule routing and Prometheus AnalysisTemplates; blue-green Rollout defined as alternative.
Alternative rejected: Flagger; plain Deployment replica ratios.
Why: Rollouts exposes abort/promote on the CR status the controller can patch; Istio gives exact percentages independent of replica count.
Consequence: Requires Istio sidecars in the agents namespace.
