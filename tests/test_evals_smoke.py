import importlib.util
import json
from pathlib import Path

from adcp.evals.gate import evaluate_gate, render_summary
from adcp.evals.metrics import ROW_FIELDS, aggregate, append_rows, arm_metrics, make_row, read_rows
from adcp.types import EvalResult, RunResult, ToolCallRecord, Usage

ROOT = Path(__file__).resolve().parents[1]


def _pair(i: int, ok: bool, cost: float, lat: float) -> tuple[RunResult, EvalResult]:
    run = RunResult(
        spec_name="s",
        spec_version="v1",
        spec_fingerprint="f",
        model_id="m",
        provider="mock",
        task_id=f"t{i}",
        cost_usd=cost,
        latency_ms=lat,
        usage=Usage(input_tokens=100, output_tokens=20, cache_read_tokens=300),
        tool_calls=[ToolCallRecord(name="a"), ToolCallRecord(name="b", ok=ok)],
    )
    return run, EvalResult(run_id=run.run_id, task_id=f"t{i}", success=ok, score=1.0 if ok else 0.5)


def test_aggregate_and_rows(tmp_path: Path) -> None:
    pairs = [_pair(i, i % 4 != 0, 0.01 * (i + 1), 100.0 * (i + 1)) for i in range(10)]
    m = aggregate(pairs)
    assert m["n"] == 10 and m["task_success"] == 0.7
    assert m["eval_score"] == 0.85 and m["cost_per_task"] == 0.055
    assert m["p50_latency_ms"] == 550.0 and m["p95_latency_ms"] == 955.0
    assert m["tool_error_rate"] == 0.15 and m["cache_hit_rate"] == 0.75
    assert m["tokens_in"] == 4000 and m["tokens_out"] == 200
    assert arm_metrics(pairs, "canary", "v2").success_rate == 0.7
    assert aggregate([])["n"] == 0
    out = tmp_path / "r.jsonl"
    append_rows(out, [make_row(m, experiment="offline", variant="v1")])
    (row,) = read_rows(out)
    assert set(row) == set(ROW_FIELDS) and row["rollback_triggered"] is None


def test_gate_thresholds() -> None:
    th = {"eval_score_min": 0.8, "task_success_min": 0.7, "cost_per_task_max": 0.01}
    good = {"eval_score": 0.9, "task_success": 0.7, "cost_per_task": 0.005, "p95_latency_ms": 9e9}
    bad = {**good, "cost_per_task": 0.02}
    assert all(c.passed for c in evaluate_gate(good, th))
    failed = [c.metric for c in evaluate_gate(bad, th) if not c.passed]
    assert failed == ["cost_per_task"]
    assert "FAIL" in render_summary("x@v2", evaluate_gate(bad, th), "runner")


def test_report(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location("report", ROOT / "scripts" / "report.py")
    assert spec and spec.loader
    report = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(report)
    runs, out = tmp_path / "runs", tmp_path / "results"
    runs.mkdir()
    rows = [
        {"experiment": "models", "variant": "a", "eval_score": 0.9, "cost_per_task": 0.01},
        {"experiment": "models", "variant": "b", "eval_score": 0.7, "cost_per_task": 0.001},
        {"experiment": "models", "variant": "c", "eval_score": 0.6, "cost_per_task": 0.02},
        {"experiment": "rollback_rule", "variant": "pareto", "false_rollbacks": 0},
    ]
    (runs / "x.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    assert report.main(["--runs", str(runs), "--out", str(out)]) == 0
    md = (out / "results.md").read_text()
    assert md.count("\n| ") == 5 and "| rollback_rule | pareto |" in md
    assert (out / "cost_vs_quality.png").stat().st_size > 10_000
    assert report.pareto_front([(0.01, 0.9), (0.001, 0.7), (0.02, 0.6)]) == [
        (0.001, 0.7),
        (0.01, 0.9),
    ]
