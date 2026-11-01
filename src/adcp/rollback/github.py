"""needs_human escalation: GitHub issue with the comparison table."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import httpx

LABELS = ["rollout", "needs-human"]


class IssueClient(Protocol):
    async def create_issue(self, title: str, body: str, labels: list[str] | None = None) -> str:
        """Return the issue URL or local path."""
        ...


def issue_title(canary_version: str) -> str:
    return f"Canary {canary_version} needs human review: trade-off"


class GitHubIssueClient:
    def __init__(
        self,
        repo: str,
        token: str,
        api: str = "https://api.github.com",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.repo, self.api = repo, api.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        self._transport = transport

    async def create_issue(self, title: str, body: str, labels: list[str] | None = None) -> str:
        async with httpx.AsyncClient(
            headers=self._headers, transport=self._transport, timeout=10.0
        ) as c:
            r = await c.post(
                f"{self.api}/repos/{self.repo}/issues",
                json={"title": title, "body": body, "labels": labels or LABELS},
            )
            r.raise_for_status()
            return str(r.json()["html_url"])


class DryRunIssueClient:
    """Writes issues to <out_dir>/<timestamp>-<slug>.md."""

    def __init__(self, out_dir: str | Path = "runs/issues") -> None:
        self.out_dir = Path(out_dir)
        self.created: list[Path] = []

    async def create_issue(self, title: str, body: str, labels: list[str] | None = None) -> str:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60]
        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        path = self.out_dir / f"{ts}-{slug}.md"
        labels_line = ", ".join(labels or LABELS)
        path.write_text(f"# {title}\n\nlabels: {labels_line}\n\n{body}\n")
        self.created.append(path)
        return str(path)


class NullIssueClient:
    """Keeps issues in memory only (experiments, chaos suite)."""

    def __init__(self) -> None:
        self.created: list[tuple[str, str]] = []

    async def create_issue(self, title: str, body: str, labels: list[str] | None = None) -> str:
        self.created.append((title, body))
        return f"memory://issue/{len(self.created)}"
