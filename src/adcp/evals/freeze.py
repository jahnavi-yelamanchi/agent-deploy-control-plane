"""Freeze the eval task set: evals/tasks/tasks.jsonl -> evals/frozen/<ver>/tasks.jsonl + MANIFEST.json.

python -m adcp.evals.freeze            # write snapshot (refuses to overwrite a different hash)
python -m adcp.evals.freeze --verify   # exit 1 if snapshot hash != MANIFEST
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SOURCE = REPO_ROOT / "evals" / "tasks" / "tasks.jsonl"
FROZEN_DIR = REPO_ROOT / "evals" / "frozen"
FROZEN_TASKS = FROZEN_DIR / "v1" / "tasks.jsonl"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def freeze(version: str = "v1", source: Path = SOURCE, force: bool = False) -> dict[str, object]:
    out = FROZEN_DIR / version
    dst, manifest_path = out / "tasks.jsonl", out / "MANIFEST.json"
    digest = sha256(source)
    if manifest_path.exists() and not force:
        old = json.loads(manifest_path.read_text())
        if old["sha256"] != digest:
            raise SystemExit(f"{manifest_path} exists with different hash; bump version or --force")
        return dict(old)
    out.mkdir(parents=True, exist_ok=True)
    data = source.read_bytes()
    dst.write_bytes(data)
    manifest = {
        "version": version,
        "sha256": digest,
        "n_tasks": sum(1 for line in data.decode().splitlines() if line.strip()),
        "source": str(source.relative_to(REPO_ROOT)),
        "created": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def verify(version: str = "v1") -> tuple[bool, str]:
    out = FROZEN_DIR / version
    manifest = json.loads((out / "MANIFEST.json").read_text())
    actual = sha256(out / "tasks.jsonl")
    if actual != manifest["sha256"]:
        return False, f"hash mismatch: manifest {manifest['sha256'][:12]} != file {actual[:12]}"
    n = sum(1 for line in (out / "tasks.jsonl").read_text().splitlines() if line.strip())
    if n != manifest["n_tasks"]:
        return False, f"task count mismatch: manifest {manifest['n_tasks']} != file {n}"
    return True, f"ok {version}: {n} tasks sha256={actual[:12]}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--version", default="v1")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)
    if args.verify:
        ok, msg = verify(args.version)
        print(msg)
        return 0 if ok else 1
    m = freeze(args.version, force=args.force)
    print(f"frozen {args.version}: {m['n_tasks']} tasks sha256={str(m['sha256'])[:12]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
