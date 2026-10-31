"""SpecRegistry on the MLflow Model Registry.

Each AgentSpec is logged as artifacts of an MLflow run (spec/agent_spec.json + .yaml) and
registered as a model version of `registered_model_name`. Stages map to aliases
candidate|canary|production plus a `stage` tag; rolled_back is a tag only (alias removed).
Local: sqlite tracking URI + .mlflow/artifacts. Services: http://mlflow:5000 with MinIO
(MLFLOW_S3_ENDPOINT_URL, AWS_ACCESS_KEY_ID/SECRET) as artifact store.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from adcp.config import get_settings
from adcp.spec import AgentSpec, Stage

os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")
EXPERIMENT = "adcp-agent-specs"
ALIASES = (Stage.candidate, Stage.canary, Stage.production)
REPO_ROOT = Path(__file__).resolve().parents[2]


class SpecRegistry:
    def __init__(
        self,
        tracking_uri: str | None = None,
        model_name: str | None = None,
        artifact_root: str | None = None,
    ) -> None:
        from mlflow.tracking import MlflowClient

        s = get_settings()
        self.uri = tracking_uri or s.mlflow_tracking_uri
        self.name = model_name or s.registered_model_name
        root = artifact_root or s.mlflow_artifact_root
        self.artifact_location: str | None = None
        if self.uri.startswith("sqlite:///"):
            db = Path(self.uri.removeprefix("sqlite:///"))
            if not db.is_absolute():
                db = REPO_ROOT / db
                self.uri = f"sqlite:///{db}"
            db.parent.mkdir(parents=True, exist_ok=True)
            art = Path(root) if Path(root).is_absolute() else REPO_ROOT / root
            art.mkdir(parents=True, exist_ok=True)
            self.artifact_location = art.as_uri()
        self.client = MlflowClient(tracking_uri=self.uri, registry_uri=self.uri)
        self._exp_id: str | None = None

    def _experiment(self) -> str:
        if self._exp_id is None:
            exp = self.client.get_experiment_by_name(EXPERIMENT)
            self._exp_id = (
                exp.experiment_id
                if exp
                else self.client.create_experiment(
                    EXPERIMENT, artifact_location=self.artifact_location
                )
            )
        return self._exp_id

    def _ensure_model(self) -> None:
        from mlflow.exceptions import MlflowException

        try:
            self.client.get_registered_model(self.name)
        except MlflowException:
            self.client.create_registered_model(
                self.name, description="AgentSpec: prompt + model + tool manifest + params"
            )

    def register(self, spec: AgentSpec) -> int:
        fp = spec.fingerprint()
        run = self.client.create_run(
            self._experiment(),
            run_name=f"{spec.name}-{spec.version}",
            tags={"adcp.spec_version": spec.version, "adcp.fingerprint": fp},
        )
        rid = run.info.run_id
        for k, v in {
            "model_id": spec.model_id,
            "provider": spec.provider.value,
            "context_strategy": spec.context_strategy.value,
            "prompt_caching": spec.params.prompt_caching,
            "n_tools": len(spec.tools),
        }.items():
            self.client.log_param(rid, k, v)
        self.client.log_dict(rid, spec.model_dump(mode="json"), "spec/agent_spec.json")
        self.client.log_text(rid, spec.to_yaml(), "spec/agent_spec.yaml")
        self.client.set_terminated(rid)
        self._ensure_model()
        mv = self.client.create_model_version(
            self.name,
            source=f"{run.info.artifact_uri}/spec",
            run_id=rid,
            tags={"stage": Stage.candidate.value, "spec_version": spec.version, "fingerprint": fp},
            description=f"{spec.name} {spec.version} ({spec.model_id})",
        )
        version = int(mv.version)
        self.client.set_registered_model_alias(self.name, Stage.candidate.value, str(version))
        return version

    def set_stage(self, version: int, stage: Stage | str) -> None:
        stage = Stage(stage)
        v = str(version)
        if stage is Stage.rolled_back:
            for alias in self._aliases_of(v):
                self.client.delete_registered_model_alias(self.name, alias)
        else:
            if stage is Stage.production:
                prev = self._alias_version(Stage.production.value)
                if prev and prev != v:
                    self.client.set_model_version_tag(self.name, prev, "stage", "superseded")
                if Stage.canary.value in self._aliases_of(v):
                    self.client.delete_registered_model_alias(self.name, Stage.canary.value)
            self.client.set_registered_model_alias(self.name, stage.value, v)
        self.client.set_model_version_tag(self.name, v, "stage", stage.value)

    def _alias_version(self, alias: str) -> str | None:
        from mlflow.exceptions import MlflowException

        try:
            return str(self.client.get_model_version_by_alias(self.name, alias).version)
        except MlflowException:
            return None

    def _aliases_of(self, version: str) -> list[str]:
        mv = self.client.get_model_version(self.name, version)
        return list(getattr(mv, "aliases", []) or [])

    def resolve(self, stage_or_version: Stage | str | int) -> int:
        if isinstance(stage_or_version, int) or str(stage_or_version).isdigit():
            return int(stage_or_version)
        alias = Stage(stage_or_version).value
        v = self._alias_version(alias)
        if v is None:
            raise KeyError(f"no version with alias {alias!r} in {self.name}")
        return int(v)

    def get(self, stage_or_version: Stage | str | int) -> AgentSpec:
        v = self.resolve(stage_or_version)
        mv = self.client.get_model_version(self.name, str(v))
        with tempfile.TemporaryDirectory() as d:
            p = self.client.download_artifacts(str(mv.run_id), "spec/agent_spec.json", d)
            return AgentSpec.model_validate(json.loads(Path(p).read_text()))

    def history(self) -> list[dict[str, Any]]:
        by_version: dict[str, list[str]] = {}
        for alias, ver in (self.client.get_registered_model(self.name).aliases or {}).items():
            by_version.setdefault(str(ver), []).append(alias)
        out = []
        for mv in self.client.search_model_versions(f"name='{self.name}'"):
            out.append(
                {
                    "version": int(mv.version),
                    "spec_version": mv.tags.get("spec_version"),
                    "fingerprint": mv.tags.get("fingerprint"),
                    "stage": mv.tags.get("stage"),
                    "aliases": sorted(by_version.get(str(mv.version), [])),
                    "created_ms": mv.creation_timestamp,
                    "run_id": mv.run_id,
                }
            )
        return sorted(out, key=lambda r: r["version"])
