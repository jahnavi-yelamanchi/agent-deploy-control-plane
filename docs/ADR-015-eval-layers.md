# ADR-015: Inspect AI for task evals, promptfoo for prompt regression

Context: Need task-level tool-use scoring and fast prompt-level assertions.
Decision: Inspect AI runs the 50 tau-bench-style tasks with a state-based scorer; promptfoo asserts prompt-level behaviour via a Python provider over the same adapter.
Alternative rejected: One framework for both; LLM-as-judge only.
Why: State-match scoring is deterministic; promptfoo assertions catch prompt regressions in seconds.
Consequence: Two configs to keep in sync with the frozen task set.
