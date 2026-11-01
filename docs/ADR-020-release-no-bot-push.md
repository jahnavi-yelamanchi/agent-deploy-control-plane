# ADR-020: Release workflow does not push to main

Context: Release must bump Helm values without CI committing to the protected branch.
Decision: release.yml registers the spec, builds the image, writes the values bump as an artifact; pushes only when `GITOPS_PUSH` and a deploy key are configured.
Alternative rejected: CI bot commits on every release.
Why: Keeps history human-authored and avoids push-triggered loops.
Consequence: GitOps bump needs a manual or configured step.
