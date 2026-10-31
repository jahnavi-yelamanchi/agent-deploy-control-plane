"""Feature flags via OpenFeature + flagd.

FLAGD_HOST[:FLAGD_PORT] set -> flagd RPC resolver (default port 8013). Otherwise flagd IN_PROCESS resolver
reading flags/flags.flagd.json offline. If that fails, an OpenFeature InMemoryProvider built from
the same JSON with a JsonLogic context evaluator. Evaluation context: track, rolloutStep.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

from openfeature import api
from openfeature.evaluation_context import EvaluationContext
from openfeature.flag_evaluation import FlagResolutionDetails, Reason
from openfeature.provider.in_memory_provider import InMemoryFlag, InMemoryProvider

from adcp.config import get_settings
from adcp.spec import AgentSpec, ContextStrategy, ToolSpec

log = logging.getLogger(__name__)
_lock = threading.Lock()
_mode: str | None = None


def _flags_path() -> Path:
    from adcp.env.tasks import resolve_path

    return resolve_path(get_settings().flags_file)


def _jsonlogic_evaluator(rule: dict[str, Any] | None) -> Any:
    def evaluate(flag: InMemoryFlag[Any], ctx: EvaluationContext) -> FlagResolutionDetails[Any]:
        variant = flag.default_variant
        if rule:
            from json_logic import jsonLogic

            data = dict(ctx.attributes or {})
            out = jsonLogic(rule, data)
            if isinstance(out, str) and out in flag.variants:
                variant = out
        reason = Reason.TARGETING_MATCH if variant != flag.default_variant else Reason.DEFAULT
        return FlagResolutionDetails(value=flag.variants[variant], variant=variant, reason=reason)

    return evaluate


def in_memory_provider(path: Path) -> InMemoryProvider:
    spec = json.loads(path.read_text())["flags"]
    flags: dict[str, InMemoryFlag[Any]] = {
        key: InMemoryFlag(
            default_variant=f["defaultVariant"],
            variants=f["variants"],
            state=InMemoryFlag.State(f.get("state", "ENABLED")),
            context_evaluator=_jsonlogic_evaluator(f.get("targeting")),
        )
        for key, f in spec.items()
    }
    return InMemoryProvider(flags)


def init_flags(force: str | None = None) -> str:
    """Set the global OpenFeature provider once. Returns the mode: rpc | in-process | in-memory."""
    global _mode
    with _lock:
        if _mode and not force:
            return _mode
        s, path = get_settings(), _flags_path()
        mode = force or ("rpc" if s.flagd_host else "in-process")
        try:
            if mode == "in-memory":
                raise RuntimeError("forced")
            from openfeature.contrib.provider.flagd import FlagdProvider
            from openfeature.contrib.provider.flagd.config import ResolverType

            if mode == "rpc":
                port = int(os.getenv("FLAGD_PORT", "8013"))
                provider = FlagdProvider(
                    host=s.flagd_host, port=port, resolver_type=ResolverType.RPC
                )
            else:
                provider = FlagdProvider(
                    resolver_type=ResolverType.IN_PROCESS, offline_flag_source_path=str(path)
                )
            api.set_provider_and_wait(provider)
            client = api.get_client()
            probe = client.get_string_details("canary-step", "__unset__", _ctx("stable", 0))
            if probe.value == "__unset__" or probe.error_code:
                raise RuntimeError(f"flagd probe failed: {probe.error_code} {probe.error_message}")
        except Exception as e:  # noqa: BLE001 - any init failure falls back
            if mode != "in-memory":
                log.info("flagd %s unavailable (%s); using InMemoryProvider", mode, e)
            mode = "in-memory"
            api.set_provider(in_memory_provider(path))
        _mode = mode
        return mode


def _ctx(track: str, rollout_step: int, **extra: Any) -> EvaluationContext:
    return EvaluationContext(
        targeting_key=extra.pop("session_id", None),
        attributes={"track": track, "rolloutStep": rollout_step, **extra},
    )


class Flags:
    def __init__(self, track: str = "stable", rollout_step: int = 0, **extra: Any) -> None:
        init_flags()
        self.client = api.get_client("adcp")
        self.ctx = _ctx(track, rollout_step, **extra)

    def bool(self, key: str, default: bool = False) -> bool:
        return bool(self.client.get_boolean_value(key, default, self.ctx))

    def string(self, key: str, default: str) -> str:
        return str(self.client.get_string_value(key, default, self.ctx))

    def enabled_tools(self, tools: list[ToolSpec]) -> list[ToolSpec]:
        return [t for t in tools if t.flag is None or self.bool(t.flag, False)]

    def context_strategy(self, spec: AgentSpec) -> ContextStrategy:
        v = self.string("context-strategy", "inherit")
        return ContextStrategy(v) if v in ContextStrategy.__members__ else spec.context_strategy

    def snapshot(self, spec: AgentSpec) -> dict[str, Any]:
        out: dict[str, Any] = {t.flag: self.bool(t.flag) for t in spec.tools if t.flag}
        out["context-strategy"] = self.string("context-strategy", "inherit")
        out["canary-step"] = self.string("canary-step", "stable")
        return out
