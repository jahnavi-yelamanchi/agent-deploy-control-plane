"""MockProvider: replays recorded trajectories (fixtures/mock/<model>.jsonl) by (task_id, variant).

Cost/latency are computed from the actual request, so prompt, tool manifest, context strategy and
prompt caching change the numbers even though the trajectory is replayed.
"""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from adcp.providers.base import ModelRequest, ModelResponse
from adcp.types import Message, ToolCall, Usage

FIXTURES_DIR = Path(__file__).with_name("fixtures") / "mock"
POLICY_MARKER = "## Policy"
# latency model (ms): base + UNCACHED_MS*uncached_in + CACHED_MS*cached_in + ms_per_tok*out
UNCACHED_MS_PER_TOKEN = 0.02
CACHED_MS_PER_TOKEN = 0.002
DEFAULT_MS_PER_OUTPUT_TOKEN = 10.0


def est_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def tools_blob(req: ModelRequest) -> str:
    return json.dumps([t.model_dump(mode="json") for t in req.tools], sort_keys=True)


def messages_blob(messages: list[Message]) -> str:
    parts = []
    for m in messages:
        parts.append(m.content)
        for tc in m.tool_calls:
            parts.append(tc.name + json.dumps(tc.arguments, sort_keys=True))
    return "\n".join(parts)


def variant_of(system: str) -> str:
    return "policy" if POLICY_MARKER in system else "no_policy"


def fixture_model_key(model_id: str) -> str:
    """Map provider-prefixed ids (anthropic.claude-sonnet-5-5, azure/gpt-5) to fixture names."""
    names = sorted((p.stem for p in FIXTURES_DIR.glob("*.jsonl")), key=len, reverse=True)
    if model_id in names:
        return model_id
    for n in names:
        if n in model_id:
            return n
    return model_id


@lru_cache(maxsize=32)
def load_fixtures(model_key: str) -> dict[tuple[str, str], dict[str, Any]]:
    path = FIXTURES_DIR / f"{model_key}.jsonl"
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text().splitlines():
        if line.strip():
            fx = json.loads(line)
            out[(fx["task_id"], fx["variant"])] = fx
    return out


class MockProvider:
    name = "mock"
    _seen_prefixes: set[str] = set()  # process-wide simulated prompt cache

    @classmethod
    def reset_cache(cls) -> None:
        cls._seen_prefixes.clear()

    def _usage(self, req: ModelRequest, output_tokens: int) -> Usage:
        prefix = req.system + tools_blob(req)
        prefix_tokens = est_tokens(prefix)
        msg_tokens = est_tokens(messages_blob(req.messages))
        if not req.params.prompt_caching:
            return Usage(input_tokens=prefix_tokens + msg_tokens, output_tokens=output_tokens)
        key = hashlib.sha256(f"{req.model_id}\x00{prefix}".encode()).hexdigest()
        if key in self._seen_prefixes:
            return Usage(
                input_tokens=msg_tokens,
                output_tokens=output_tokens,
                cache_read_tokens=prefix_tokens,
            )
        self._seen_prefixes.add(key)
        return Usage(
            input_tokens=msg_tokens, output_tokens=output_tokens, cache_write_tokens=prefix_tokens
        )

    @staticmethod
    def _latency(usage: Usage, base_ms: float, ms_per_tok: float) -> float:
        uncached = usage.input_tokens + usage.cache_write_tokens
        return round(
            base_ms
            + UNCACHED_MS_PER_TOKEN * uncached
            + CACHED_MS_PER_TOKEN * usage.cache_read_tokens
            + ms_per_tok * usage.output_tokens,
            2,
        )

    def _final(self, answer: str, actions: list[str], needs_human: bool) -> str:
        return json.dumps({"answer": answer, "actions_taken": actions, "needs_human": needs_human})

    async def generate(self, req: ModelRequest) -> ModelResponse:
        task_id = str(req.metadata.get("task_id") or "")
        fx = load_fixtures(fixture_model_key(req.model_id)).get((task_id, variant_of(req.system)))
        step = sum(m.role == "assistant" for m in req.messages)
        tool_names = {t.name for t in req.tools}
        ms_tok = DEFAULT_MS_PER_OUTPUT_TOKEN
        if fx is None:
            text = self._final(
                "I can help with reservations and orders. Please share your user id and email.",
                [],
                False,
            )
            usage = self._usage(req, 40)
            return self._resp(req, Message(role="assistant", content=text), usage, 400.0, ms_tok)
        ms_tok = float(fx.get("ms_per_output_token", ms_tok))
        steps: list[dict[str, Any]] = fx["steps"]
        if step < len(steps):
            s = steps[step]
            if s["tool"] in tool_names:
                msg = Message(
                    role="assistant",
                    content=s.get("thought", ""),
                    tool_calls=[
                        ToolCall(id=f"call_{step}", name=s["tool"], arguments=s["arguments"])
                    ],
                )
                usage = self._usage(req, int(s["output_tokens"]))
                return self._resp(req, msg, usage, float(s["base_ms"]), ms_tok, "tool_use")
            text = self._final(
                f"Sorry, I can't complete this request: the {s['tool']} capability is unavailable.",
                [st["tool"] for st in steps[:step]],
                True,
            )
            usage = self._usage(req, 45)
            return self._resp(req, Message(role="assistant", content=text), usage, 300.0, ms_tok)
        fin = fx["final"]
        text = self._final(
            fin["answer"], fin.get("actions_taken", []), fin.get("needs_human", False)
        )
        usage = self._usage(req, int(fin["output_tokens"]))
        return self._resp(
            req, Message(role="assistant", content=text), usage, float(fin["base_ms"]), ms_tok
        )

    def _resp(
        self,
        req: ModelRequest,
        msg: Message,
        usage: Usage,
        base_ms: float,
        ms_tok: float,
        finish: str = "end_turn",
    ) -> ModelResponse:
        return ModelResponse(
            message=msg,
            usage=usage,
            latency_ms=self._latency(usage, base_ms, ms_tok),
            finish_reason=finish,
            model_id=req.model_id,
            provider=self.name,
        )
