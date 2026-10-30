# ADR-005: Tools served over MCP

Context: Tool manifest must be portable across providers and deployable separately from the runtime.
Decision: Tools live in a FastMCP server; runtime calls them through an MCP client (in-process locally, stdio/HTTP in cluster).
Alternative rejected: Provider-native function calling against in-process Python functions.
Why: MCP decouples tool versioning from the runtime image and is supported by every provider in the matrix.
Consequence: One extra hop per tool call in cluster mode.
