"""Argo Rollouts actions via the Kubernetes API (same patches as the kubectl plugin).

abort:         merge-patch rollouts/<name>/status  {"status": {"abort": true}}
promote:       merge-patch rollouts/<name>/status  {"status": {"pauseConditions": null}}
               + merge-patch rollouts/<name>       {"spec": {"paused": false}}
promote --full: merge-patch rollouts/<name>/status {"status": {"promoteFull": true}}
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Protocol

import httpx

SA_DIR = Path("/var/run/secrets/kubernetes.io/serviceaccount")
MERGE_PATCH = {"Content-Type": "application/merge-patch+json"}
REVISION_ANNOTATION = "rollout.argoproj.io/revision"


class RolloutsClient(Protocol):
    async def abort(self, reason: str = "") -> dict[str, Any]: ...
    async def promote(self, full: bool = True) -> dict[str, Any]: ...
    async def get_status(self) -> dict[str, Any]: ...


def summarize(rollout: dict[str, Any]) -> dict[str, Any]:
    """Rollout object -> {revision, phase, step, weight, aborted, stable_hash, canary_hash}."""
    meta, status = rollout.get("metadata", {}), rollout.get("status", {})
    canary = status.get("canary", {}) or {}
    weights = canary.get("weights", {}) or {}
    return {
        "revision": meta.get("annotations", {}).get(REVISION_ANNOTATION, ""),
        "phase": status.get("phase", ""),
        "step": status.get("currentStepIndex"),
        "weight": (weights.get("canary") or {}).get("weight"),
        "aborted": bool(status.get("abort", False)),
        "stable_hash": status.get("stableRS", ""),
        "canary_hash": status.get("currentPodHash", ""),
    }


class ArgoRolloutsClient:
    def __init__(
        self,
        name: str = "agent-runtime",
        namespace: str = "agents",
        base_url: str = "https://kubernetes.default.svc",
        token: str | None = None,
        verify: str | bool = True,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.name, self.namespace = name, namespace
        self.base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._verify = verify
        self._transport = transport

    @classmethod
    def in_cluster(cls, name: str, namespace: str, sa_dir: Path = SA_DIR) -> ArgoRolloutsClient:
        """ServiceAccount token + CA; needs RBAC patch on rollouts and rollouts/status."""
        host = os.environ.get("KUBERNETES_SERVICE_HOST", "kubernetes.default.svc")
        port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
        return cls(
            name=name,
            namespace=namespace,
            base_url=f"https://{host}:{port}",
            token=(sa_dir / "token").read_text().strip(),
            verify=str(sa_dir / "ca.crt"),
        )

    @property
    def path(self) -> str:
        return f"/apis/argoproj.io/v1alpha1/namespaces/{self.namespace}/rollouts/{self.name}"

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_url,
            headers=self._headers,
            verify=self._verify,
            transport=self._transport,
            timeout=10.0,
        )

    async def _patch(self, sub: str, body: dict[str, Any]) -> dict[str, Any]:
        async with self._client() as c:
            r = await c.patch(self.path + sub, json=body, headers=MERGE_PATCH)
            r.raise_for_status()
            return r.json()  # type: ignore[no-any-return]

    async def abort(self, reason: str = "") -> dict[str, Any]:
        return summarize(await self._patch("/status", {"status": {"abort": True}}))

    async def promote(self, full: bool = True) -> dict[str, Any]:
        if full:
            return summarize(await self._patch("/status", {"status": {"promoteFull": True}}))
        await self._patch("/status", {"status": {"pauseConditions": None}})
        return summarize(await self._patch("", {"spec": {"paused": False}}))

    async def get_status(self) -> dict[str, Any]:
        async with self._client() as c:
            r = await c.get(self.path)
            r.raise_for_status()
            return summarize(r.json())


class DryRunArgoClient:
    """Records calls; keeps a fake rollout state for local runs and the demo."""

    def __init__(self, revision: str = "1") -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.state: dict[str, Any] = {
            "revision": revision,
            "phase": "Progressing",
            "step": 0,
            "weight": 0,
            "aborted": False,
        }

    def new_revision(self, revision: str) -> None:
        self.state.update(revision=revision, phase="Progressing", step=0, weight=0, aborted=False)

    def set_weight(self, weight: int) -> None:
        self.state["weight"] = weight

    async def abort(self, reason: str = "") -> dict[str, Any]:
        self.calls.append(("abort", {"status": {"abort": True}, "reason": reason}))
        self.state.update(aborted=True, phase="Degraded", weight=0)
        return dict(self.state)

    async def promote(self, full: bool = True) -> dict[str, Any]:
        body = {"status": {"promoteFull": True}} if full else {"status": {"pauseConditions": None}}
        self.calls.append(("promote", body))
        if full:
            self.state.update(phase="Healthy", weight=100)
        else:
            self.state["step"] = (self.state.get("step") or 0) + 1
        return dict(self.state)

    async def get_status(self) -> dict[str, Any]:
        return dict(self.state)
