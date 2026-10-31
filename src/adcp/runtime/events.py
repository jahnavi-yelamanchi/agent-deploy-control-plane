"""Event bus: Kafka/Redpanda via aiokafka (backend=services) or in-process fallback.

Topics: agent.runs (RunResult), agent.evals (EvalResult + track + spec_version),
agent.rollbacks (decision). Interface: await publish(topic, model|dict); async for x in subscribe(topic).
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from collections.abc import AsyncIterator
from typing import Any, Protocol

from pydantic import BaseModel

from adcp.config import get_settings

TOPIC_RUNS = "agent.runs"
TOPIC_EVALS = "agent.evals"
TOPIC_ROLLBACKS = "agent.rollbacks"
TOPICS = (TOPIC_RUNS, TOPIC_EVALS, TOPIC_ROLLBACKS)


def _encode(payload: BaseModel | dict[str, Any]) -> bytes:
    if isinstance(payload, BaseModel):
        return payload.model_dump_json().encode()
    return json.dumps(payload, default=str).encode()


class EventBus(Protocol):
    async def publish(self, topic: str, payload: BaseModel | dict[str, Any]) -> None: ...
    def subscribe(self, topic: str) -> AsyncIterator[dict[str, Any]]: ...
    async def close(self) -> None: ...


class InMemoryEventBus:
    """Fan-out to live subscribers; keeps history so late subscribers can replay (from_start)."""

    def __init__(self) -> None:
        self.history: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._subs: dict[str, list[asyncio.Queue[dict[str, Any]]]] = defaultdict(list)

    async def publish(self, topic: str, payload: BaseModel | dict[str, Any]) -> None:
        msg = json.loads(_encode(payload))
        self.history[topic].append(msg)
        for q in self._subs[topic]:
            q.put_nowait(msg)

    async def subscribe(
        self, topic: str, from_start: bool = False
    ) -> AsyncIterator[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        if from_start:
            for m in self.history[topic]:
                q.put_nowait(m)
        self._subs[topic].append(q)
        try:
            while True:
                yield await q.get()
        finally:
            self._subs[topic].remove(q)

    async def close(self) -> None:
        self._subs.clear()


class KafkaEventBus:
    def __init__(self, bootstrap: str | None = None, group_id: str = "adcp") -> None:
        self.bootstrap = bootstrap or get_settings().kafka_bootstrap
        self.group_id = group_id
        self._producer: Any = None

    async def _get_producer(self) -> Any:
        if self._producer is None:
            from aiokafka import AIOKafkaProducer

            self._producer = AIOKafkaProducer(
                bootstrap_servers=self.bootstrap, acks="all", enable_idempotence=True
            )
            await self._producer.start()
        return self._producer

    async def publish(self, topic: str, payload: BaseModel | dict[str, Any]) -> None:
        key = None
        if isinstance(payload, BaseModel):
            key = getattr(payload, "run_id", None)
        prod = await self._get_producer()
        await prod.send_and_wait(topic, _encode(payload), key=key.encode() if key else None)

    async def subscribe(
        self, topic: str, from_start: bool = False
    ) -> AsyncIterator[dict[str, Any]]:
        from aiokafka import AIOKafkaConsumer

        consumer = AIOKafkaConsumer(
            topic,
            bootstrap_servers=self.bootstrap,
            group_id=self.group_id,
            auto_offset_reset="earliest" if from_start else "latest",
            value_deserializer=lambda b: json.loads(b),
        )
        await consumer.start()
        try:
            async for rec in consumer:
                yield rec.value
        finally:
            await consumer.stop()

    async def close(self) -> None:
        if self._producer is not None:
            await self._producer.stop()
            self._producer = None


_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    global _bus
    if _bus is None:
        _bus = KafkaEventBus() if get_settings().backend == "services" else InMemoryEventBus()
    return _bus
