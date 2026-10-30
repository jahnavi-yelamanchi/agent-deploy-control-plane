# ADR-001: AgentSpec is the deployable unit

Context: Prompt, model and tool changes ship independently today and regress silently.
Decision: One Pydantic `AgentSpec` (prompt, model, provider, tool manifest, context strategy, params, budget) with a content fingerprint is the unit that is versioned, evaluated, rolled out and rolled back.
Alternative rejected: Versioning prompts in a prompt CMS and models in deploy config separately.
Why: A regression is usually an interaction (prompt x model x tools); only a joint artifact makes a rollback restore a known-good combination.
Consequence: Every change, even a one-word prompt edit, goes through the eval gate and a canary.
