# ADR-019: LitmusChaos plus in-process fault injection

Context: Need to measure time to detect and time to roll back.
Decision: LitmusChaos experiments (pod-delete, network latency) for cluster; Python injector (degraded prompt, latency, killed tool) for the in-process loop.
Alternative rejected: Chaos Mesh.
Why: Litmus has Prometheus probes; the injector makes MTTD/MTTR measurable without a cluster.
Consequence: In-process MTTD is simulated time.
