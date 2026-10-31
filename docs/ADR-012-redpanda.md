# ADR-012: Redpanda for the Kafka API

Context: Run events must reach the evaluator and rollback controller asynchronously.
Decision: Kafka protocol via aiokafka; Redpanda in compose, any Kafka in cloud.
Alternative rejected: Apache Kafka + ZooKeeper/KRaft locally; Redis streams.
Why: Single binary, fast start on a laptop, wire-compatible with MSK/Event Hubs.
Consequence: In-process bus used for `demo-local` with the same interface.
