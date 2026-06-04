#!/usr/bin/env python3
"""Inject shared fixture staging setup into experiment YAML variants."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_PATHS = (
    REPO_ROOT / "experiments" / "financialdatasets-vs-edgar-latest-reported-financials.yaml",
    REPO_ROOT / "examples" / "financialdatasets-vs-edgar-latest-reported-financials.yaml",
)
ANCHOR_NAME = "&fixture_setup"
ALIAS_REF = "*fixture_setup"
SETUP_REF = f"        setup: {ALIAS_REF}"
HEADER = (
    "# Staging setup is generated — run: python3 scripts/sync-experiment-fixture-setup.py\n"
)


def load_setup_script() -> str:
    render = REPO_ROOT / "scripts" / "render-container-fixture-setup.py"
    return subprocess.check_output([sys.executable, str(render)], text=True)


def indent_block(text: str, prefix: str = "          ") -> str:
    return "".join(f"{prefix}{line}" if line else "\n" for line in text.splitlines(keepends=True))


def strip_existing_fixture_setup(text: str) -> str:
    text = re.sub(
        rf"^        setup: {re.escape(ANCHOR_NAME)} \|.*?(?=^    - id: |^model:\n)",
        "",
        text,
        count=1,
        flags=re.MULTILINE | re.DOTALL,
    )
    text = re.sub(rf"^        setup: {re.escape(ALIAS_REF)}\n", "", text, flags=re.MULTILINE)
    text = re.sub(
        r"^x-fixture-setup: &fixture_setup \|.*?^(?:x-fixture-setup-end|  # x-fixture-setup-end)\n",
        "",
        text,
        count=1,
        flags=re.MULTILINE | re.DOTALL,
    )
    return text


def ensure_staged_fixtures_check(text: str) -> str:
    if "staged-fixtures-present" in text:
        return text
    if "stage-eval-fixtures" in text:
        text = text.replace("stage-eval-fixtures", "staged-fixtures-present", 1)
        text = re.sub(
            r"  - name: staged-fixtures-present\n    script: \|.*?(?=  - name: eval-fixtures-available)",
            "  - name: staged-fixtures-present\n    script: |\n"
            "      set -e\n"
            "      test -f /opt/axp-eval/fixtures/latest-reporting-period-core.json\n"
            "      test -f /opt/axp-eval/scripts/eval_bootstrap.py\n"
            "      mkdir -p /workspace/fixtures /workspace/scripts\n"
            "      cp -a /opt/axp-eval/fixtures/. /workspace/fixtures/\n"
            "      cp -a /opt/axp-eval/scripts/. /workspace/scripts/\n",
            text,
            count=1,
            flags=re.DOTALL,
        )
    return text


def inject_setup(text: str, setup_script: str) -> str:
    first_variant = re.search(
        r"(    - id: [^\n]+\n      tag: [^\n]+\n      overrides:\n)",
        text,
    )
    if not first_variant:
        raise ValueError("could not find first variant overrides block")
    anchor_block = (
        f"        setup: {ANCHOR_NAME} |\n"
        f"{indent_block(setup_script)}"
    )
    start = first_variant.end()
    text = text[:start] + anchor_block + text[start:]

    def add_alias(match: re.Match[str]) -> str:
        block = match.group(0)
        if ALIAS_REF in block or ANCHOR_NAME in block:
            return block
        return block.replace("      overrides:\n", f"      overrides:\n{SETUP_REF}\n", 1)

    return re.sub(
        r"    - id: [^\n]+\n      tag: [^\n]+\n      overrides:\n(?:        [^\n]+\n)*",
        add_alias,
        text,
    )


def sync_file(path: Path, setup_script: str) -> None:
    text = path.read_text(encoding="utf-8")
    if text.startswith(HEADER):
        text = text[len(HEADER) :]
    text = strip_existing_fixture_setup(text)
    text = ensure_staged_fixtures_check(text)
    text = inject_setup(text, setup_script)
    path.write_text(HEADER + text, encoding="utf-8")


def main() -> None:
    setup_script = load_setup_script()
    for path in EXPERIMENT_PATHS:
        sync_file(path, setup_script)
        print(f"updated {path.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
