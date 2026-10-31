"""Record provider fixtures for MockProvider: src/adcp/providers/fixtures/mock/<model_id>.jsonl.

MODEL_PROVIDER=mock (default): synthesize trajectories deterministically from each task's
expected_actions using per-model profiles. A latent per-task difficulty (shared across models
80% of the time, per-model otherwise) decides success, so a stronger model/prompt fails a subset of the
tasks a weaker one fails. Failure modes: wrong_argument, skipped_verification, missing_output.

MODEL_PROVIDER=real: run each specs/matrix spec (policy prompt) and its no-policy twin against
the real provider through the harness and record the observed trajectory.

Usage: python scripts/record_fixtures.py [--models m1,m2] [--seed N]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
from pathlib import Path
from typing import Any

from adcp.env.tasks import Task, load_tasks
from adcp.env.tools import MUTATING
from adcp.providers.mock import FIXTURES_DIR

SEED = 13
# p_policy / p_no_policy: success prob. base_ms: per-call time-to-first-token. tps: output tok/s.
# out_scale: output token multiplier (reasoning-heavy models emit more).
PROFILES: dict[str, dict[str, float]] = {
    "claude-sonnet-5-5": {
        "p_policy": 0.92,
        "p_no_policy": 0.62,
        "base_ms": 450,
        "tps": 90,
        "out_scale": 1.0,
    },
    "claude-haiku-5-5": {
        "p_policy": 0.80,
        "p_no_policy": 0.50,
        "base_ms": 250,
        "tps": 180,
        "out_scale": 0.9,
    },
    "gpt-5": {"p_policy": 0.90, "p_no_policy": 0.60, "base_ms": 600, "tps": 80, "out_scale": 1.3},
    "gemini-2.5-pro": {
        "p_policy": 0.86,
        "p_no_policy": 0.56,
        "base_ms": 550,
        "tps": 100,
        "out_scale": 1.2,
    },
    "llama-3.1-8b-instruct": {
        "p_policy": 0.66,
        "p_no_policy": 0.40,
        "base_ms": 150,
        "tps": 120,
        "out_scale": 1.0,
    },
    "qwen2.5-7b-instruct": {
        "p_policy": 0.64,
        "p_no_policy": 0.38,
        "base_ms": 140,
        "tps": 130,
        "out_scale": 1.0,
    },
}
NO_POLICY_VERBOSITY = 1.6  # degraded prompt -> rambling outputs
FAILURE_WEIGHTS = {
    "policy": {"wrong_argument": 0.5, "missing_output": 0.3, "skipped_verification": 0.2},
    "no_policy": {"skipped_verification": 0.5, "wrong_argument": 0.3, "missing_output": 0.2},
}
TEMPLATES = {
    "cancel_reservation": "Reservation {0} is cancelled. A refund of ${2} goes to the original payment method.",
    "change_flight": "Your reservation now uses flight {0}; the fare difference was applied.",
    "cancel_flown": "I cannot cancel reservation {1}: it has already been flown.",
    "reservation_status": "Your reservation is on flight {0} and its status is {1}.",
    "delay_compensation": "I've issued $100 compensation for the delay.",
    "cancel_order": "Order {0} is cancelled and will be refunded.",
    "return_items": "The return is requested; ${0} will be refunded to your gift card.",
    "exchange_items": "Your exchange is requested for the {1} version.",
    "order_status": "Order {0} is {1}.",
    "cancel_delivered": "I cannot cancel a delivered order, but I can start a return for it.",
    "transfer": "I've transferred you to a human agent who will follow up.",
}


def _rng(*parts: Any) -> random.Random:
    h = hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()
    return random.Random(int(h[:16], 16))


def _alt(values: list[str], current: str) -> str | None:
    others = [v for v in values if v != current]
    return sorted(others)[0] if others else None


def wrong_argument(task: Task, actions: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    db = task.initial_state
    for i, a in enumerate(actions):
        if a["tool"] not in MUTATING:
            continue
        args = dict(a["arguments"])
        if "flight_numbers" in args:
            cur = db["flights"][args["flight_numbers"][0]]
            same = [
                f
                for f, v in db["flights"].items()
                if (v["origin"], v["destination"], v["date"], v["status"])
                == (cur["origin"], cur["destination"], cur["date"], "scheduled")
            ]
            alt = _alt(same, args["flight_numbers"][0])
            if alt:
                args["flight_numbers"] = [alt]
        elif "new_item_ids" in args:
            order = db["orders"][args["order_id"]]
            pid = next(
                x["product_id"] for x in order["items"] if x["item_id"] == args["item_ids"][0]
            )
            alts = [v for v in db["products"][pid]["variants"] if v not in args["new_item_ids"]]
            alts = [v for v in alts if v != args["item_ids"][0]] or alts
            if alts:
                args["new_item_ids"] = [sorted(alts)[0]]
        elif "item_ids" in args:
            items = [x["item_id"] for x in db["orders"][args["order_id"]]["items"]]
            alt = _alt(items, args["item_ids"][0])
            if alt:
                args["item_ids"] = [alt]
        elif "amount" in args:
            args["amount"] = round(args["amount"] * 1.5, 2)
        elif "reservation_id" in args:
            alt = _alt(list(db["reservations"]), args["reservation_id"])
            if alt:
                args["reservation_id"] = alt
        elif "order_id" in args:
            alt = _alt(list(db["orders"]), args["order_id"])
            if alt:
                args["order_id"] = alt
        if args == a["arguments"]:
            return None
        return [*actions[:i], {"tool": a["tool"], "arguments": args}, *actions[i + 1 :]]
    return None


def skipped_verification(task: Task, actions: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    if not any(a["tool"] in MUTATING for a in actions):
        return None
    if not any(a["tool"] == "verify_identity" for a in actions):
        return None
    return [a for a in actions if a["tool"] != "verify_identity"]


def answer_text(task: Task, drop_first: bool) -> str:
    outs = list(task.expected_outputs)
    if drop_first:
        rest = outs[1:]
        return "Your request has been processed." + (
            f" Details: {', '.join(rest)}." if rest else ""
        )
    tmpl = TEMPLATES.get(task.tags[1] if len(task.tags) > 1 else "", "")
    try:
        return tmpl.format(*outs) if tmpl else "Done: " + ", ".join(outs)
    except IndexError:
        return "Done: " + ", ".join(outs)


def synth(task: Task, model: str, variant: str, seed: int) -> dict[str, Any]:
    prof = PROFILES[model]
    # mixture of uniforms is uniform: P(success) = p, 80% of draws shared across models
    nr = _rng(seed, "noise", model, task.id)
    u = _rng(seed, "difficulty", task.id).random() if nr.random() < 0.8 else nr.random()
    ok = u < prof[f"p_{variant}"]
    actions = [dict(a) for a in task.expected_actions]
    drop_output, mode = False, "none"
    if not ok:
        r = _rng(seed, "mode", model, task.id, variant)
        weights = FAILURE_WEIGHTS[variant]
        order = sorted(weights, key=lambda k: -weights[k] * r.random())
        for mode in [*order, "missing_output"]:
            if mode == "missing_output":
                drop_output = True
                break
            changed = (
                wrong_argument(task, actions)
                if mode == "wrong_argument"
                else (skipped_verification(task, actions))
            )
            if changed is not None:
                actions = changed
                break
    # latency/tokens paired across variants: same rng stream per (model, task)
    lr = _rng(seed, "latency", model, task.id)
    verb = NO_POLICY_VERBOSITY if variant == "no_policy" else 1.0
    scale = prof["out_scale"] * verb

    def base() -> float:
        return round(prof["base_ms"] * lr.lognormvariate(0, 0.25), 1)

    steps = [
        {
            "tool": a["tool"],
            "arguments": a["arguments"],
            "output_tokens": int(lr.randint(30, 60) * scale),
            "base_ms": base(),
        }
        for a in actions
    ]
    final = {
        "answer": answer_text(task, drop_output),
        "actions_taken": [a["tool"] for a in actions if a["tool"] in MUTATING],
        "needs_human": any(a["tool"] == "transfer_to_human" for a in actions),
        "output_tokens": int(lr.randint(60, 110) * scale),
        "base_ms": base(),
    }
    return {
        "task_id": task.id,
        "variant": variant,
        "outcome": "success" if ok else mode,
        "ms_per_output_token": round(1000.0 / prof["tps"], 3),
        "steps": steps,
        "final": final,
    }


def record_mock(models: list[str], seed: int) -> None:
    tasks = load_tasks()
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    for m in models:
        lines = [
            json.dumps(synth(t, m, v, seed), separators=(",", ":"))
            for t in tasks
            for v in ("policy", "no_policy")
        ]
        (FIXTURES_DIR / f"{m}.jsonl").write_text("\n".join(lines) + "\n")
        print(f"{m}: {len(lines)} fixtures")


def record_real(models: list[str]) -> None:
    from adcp.runtime.harness import run_task
    from adcp.spec import AgentSpec

    repo = Path(__file__).resolve().parents[1]
    no_policy_prompt = AgentSpec.from_yaml(repo / "specs" / "v2-bad.yaml").system_prompt
    specs = {
        s.model_id: s
        for s in map(AgentSpec.from_yaml, sorted((repo / "specs/matrix").glob("*.yaml")))
    }
    for m in models:
        if m not in specs:
            print(f"skip {m}: no specs/matrix spec")
            continue
        lines = []
        for t in load_tasks():
            for variant in ("policy", "no_policy"):
                spec = specs[m]
                if variant == "no_policy":
                    spec = spec.model_copy(update={"system_prompt": no_policy_prompt})
                rr, ev = run_task(spec, t)
                n = max(1, rr.steps)
                per_out, per_ms = rr.usage.output_tokens // n, rr.latency_ms / n
                steps = [
                    {
                        "tool": c.name,
                        "arguments": c.arguments,
                        "output_tokens": per_out,
                        "base_ms": per_ms,
                    }
                    for c in rr.tool_calls
                ]
                st = rr.structured or {}
                lines.append(
                    json.dumps(
                        {
                            "task_id": t.id,
                            "variant": variant,
                            "outcome": "success" if ev.success else "recorded_failure",
                            "ms_per_output_token": 0.0,
                            "steps": steps,
                            "final": {
                                "answer": st.get("answer", rr.output),
                                "actions_taken": st.get("actions_taken", []),
                                "needs_human": st.get("needs_human", False),
                                "output_tokens": per_out,
                                "base_ms": per_ms,
                            },
                        },
                        separators=(",", ":"),
                    )
                )
        (FIXTURES_DIR / f"{m}.jsonl").write_text("\n".join(lines) + "\n")
        print(f"{m}: recorded {len(lines)} real trajectories")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default=",".join(PROFILES))
    ap.add_argument("--seed", type=int, default=SEED)
    a = ap.parse_args()
    models = [m for m in a.models.split(",") if m]
    if os.getenv("MODEL_PROVIDER", "mock").lower() == "real":
        record_real(models)
    else:
        record_mock(models, a.seed)


if __name__ == "__main__":
    main()
