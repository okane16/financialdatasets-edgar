#!/usr/bin/env python3
"""Emit a variant `setup` bash script that stages fixtures into /workspace.

AXP sandboxes do not bind-mount the host repo; graders need
`/workspace/fixtures` and `/workspace/scripts` before setup_checks run.
"""

from __future__ import annotations

import base64
import io
import tarfile
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTAINER_ROOT = Path("/opt/axp-eval")
FIXTURES_DIR = REPO_ROOT / "fixtures"
SCRIPT_FILES = (
    "eval_bootstrap.py",
    "fixture_paths.py",
    "ticker_registry.py",
)
CHUNK_SIZE = 76


def build_tarball() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for path in sorted(FIXTURES_DIR.rglob("*")):
            if path.is_file():
                arcname = Path("fixtures") / path.relative_to(FIXTURES_DIR)
                tar.add(path, arcname=str(arcname))
        for name in SCRIPT_FILES:
            path = REPO_ROOT / "scripts" / name
            if not path.is_file():
                raise FileNotFoundError(path)
            tar.add(path, arcname=f"scripts/{name}")
    return buf.getvalue()


def render_setup() -> str:
    encoded = base64.b64encode(build_tarball()).decode("ascii")
    chunks = textwrap.wrap(encoded, CHUNK_SIZE)
    chunk_lines = "\n".join(f'    "{chunk}",' for chunk in chunks)
    return (
        "set -euo pipefail\n"
        "python3 - <<'PY'\n"
        "import base64, io, tarfile\nfrom pathlib import Path\n"
        "payload = \"\".join([\n"
        f"{chunk_lines}\n"
        "])\n"
        "root = Path('/opt/axp-eval')\n"
        "root.mkdir(parents=True, exist_ok=True)\n"
        "data = base64.b64decode(payload)\n"
        f"tarfile.open(fileobj=io.BytesIO(data), mode='r:gz').extractall(root)\n"
        "PY\n"
    )


def main() -> None:
    print(render_setup(), end="")


if __name__ == "__main__":
    main()
