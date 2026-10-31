"""Task model + loader for the tau-style eval set (evals/tasks/tasks.jsonl)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

REPO_ROOT = Path(__file__).resolve().parents[3]


def resolve_path(p: str | Path) -> Path:
    """Relative paths: cwd first (container WORKDIR), then the source checkout."""
    p = Path(p)
    if p.is_absolute() or p.exists():
        return p
    return REPO_ROOT / p


TASKS_PATH = resolve_path(os.getenv("ADCP_TASKS_PATH", "evals/tasks/tasks.jsonl"))


class Action(BaseModel):
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class Task(BaseModel):
    id: str
    domain: Literal["airline", "retail"]
    user_input: str
    initial_state: dict[str, Any]
    expected_actions: list[dict[str, Any]] = Field(default_factory=list)  # [{tool, arguments}]
    expected_outputs: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


def load_tasks(path: str | Path | None = None) -> list[Task]:
    p = Path(path) if path else TASKS_PATH
    return [Task.model_validate(json.loads(line)) for line in p.read_text().splitlines() if line]


def get_task(task_id: str, path: str | Path | None = None) -> Task:
    for t in load_tasks(path):
        if t.id == task_id:
            return t
    raise KeyError(task_id)
