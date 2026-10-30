# ADR-004: LangGraph for the agent loop

Context: Need an explicit, inspectable tool-use loop with step limits and budget checks.
Decision: LangGraph `StateGraph` (plan -> call_model -> call_tools -> finalize); LangChain Deep Agents kept as an optional builder for the planning pattern.
Alternative rejected: Hand-rolled while loop; Deep Agents as the default runtime.
Why: Graph nodes map 1:1 to spans and budget checks; Deep Agents pulls LangChain chat models and a virtual FS the eval does not need.
Consequence: Provider calls bypass LangChain model wrappers and go through the adapter interface.
