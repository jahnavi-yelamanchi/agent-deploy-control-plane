"""Inspect AI entrypoint.

inspect eval evals/inspect/tau_suite.py@tau_mock --model mockllm/model -T spec_path=specs/v2-bad.yaml
"""

from inspect_ai import Task, task

from adcp.evals.inspect_suite import build_task


@task
def tau_mock(spec_path: str = "specs/v1.yaml", tasks_path: str | None = None) -> Task:
    return build_task(spec_path, tasks_path)
