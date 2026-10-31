"""adcp CLI: run | register | promote | history | serve | mcp."""

from __future__ import annotations

import argparse
import json
import sys

from adcp.spec import AgentSpec, Stage


def _run(a: argparse.Namespace) -> int:
    from adcp.env.tasks import get_task
    from adcp.runtime.harness import run_task

    spec = AgentSpec.from_yaml(a.spec)
    rr, ev = run_task(spec, get_task(a.task), track=a.track, rollout_step=a.rollout_step)
    out = {
        "run": rr.model_dump(mode="json", exclude={"tool_calls"}),
        "tools": [f"{t.name}:{'ok' if t.ok else 'err'}" for t in rr.tool_calls],
        "eval": ev.model_dump(mode="json"),
    }
    print(json.dumps(out, indent=2, default=str))
    return 0


def _register(a: argparse.Namespace) -> int:
    from adcp.registry import SpecRegistry

    reg = SpecRegistry()
    v = reg.register(AgentSpec.from_yaml(a.spec))
    if a.stage:
        reg.set_stage(v, Stage(a.stage))
    print(json.dumps({"version": v, "stage": a.stage or "candidate"}))
    return 0


def _promote(a: argparse.Namespace) -> int:
    from adcp.registry import SpecRegistry

    SpecRegistry().set_stage(a.version, Stage(a.stage))
    print(json.dumps({"version": a.version, "stage": a.stage}))
    return 0


def _history(a: argparse.Namespace) -> int:
    from adcp.registry import SpecRegistry

    print(json.dumps(SpecRegistry().history(), indent=2))
    return 0


def _serve(a: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run("adcp.runtime.app:app", host=a.host, port=a.port)
    return 0


def _mcp(a: argparse.Namespace) -> int:
    from adcp.runtime.mcp_server import main

    main()
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="adcp", description="Agent Deployment Control Plane")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run one task with a spec and score it")
    r.add_argument("--spec", default="specs/v1.yaml")
    r.add_argument("--task", default="airline-001")
    r.add_argument("--track", choices=["stable", "canary"], default="stable")
    r.add_argument("--rollout-step", type=int, default=None)
    r.set_defaults(fn=_run)
    g = sub.add_parser("register", help="log spec to the MLflow registry")
    g.add_argument("--spec", required=True)
    g.add_argument("--stage", choices=[s.value for s in Stage], default=None)
    g.set_defaults(fn=_register)
    m = sub.add_parser("promote", help="set a registry version's stage")
    m.add_argument("--version", type=int, required=True)
    m.add_argument("--stage", choices=[s.value for s in Stage], required=True)
    m.set_defaults(fn=_promote)
    sub.add_parser("history", help="registry versions").set_defaults(fn=_history)
    s = sub.add_parser("serve", help="run the FastAPI runtime")
    s.add_argument("--host", default="0.0.0.0")
    s.add_argument("--port", type=int, default=8080)
    s.set_defaults(fn=_serve)
    sub.add_parser("mcp", help="serve the tau MCP server (stdio)").set_defaults(fn=_mcp)
    a = p.parse_args(argv)
    return int(a.fn(a))


if __name__ == "__main__":
    sys.exit(main())
