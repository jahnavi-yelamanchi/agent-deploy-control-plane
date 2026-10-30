# ADR-008: Argo CD for GitOps

Context: Cluster state must be reproducible from the repo.
Decision: Argo CD app-of-apps; release workflow bumps Helm values, Argo CD syncs.
Alternative rejected: Flux; `helm upgrade` from CI.
Why: Same project family as Rollouts, UI shows rollout health; CI holds no cluster credentials.
Consequence: Image bump commit is produced by CI as an artifact and pushed only when a deploy key is configured.
