# ADR-013: OpenFeature with flagd

Context: New tools and context strategies must be gated per rollout step without redeploys.
Decision: OpenFeature SDK + flagd provider; flags in `flags/flags.flagd.json`, targeting on `track` and `rolloutStep`; in-process resolver offline locally.
Alternative rejected: LaunchDarkly; env vars.
Why: Vendor-neutral API, flag file lives in Git, no SaaS account.
Consequence: Flag changes are GitOps changes.
