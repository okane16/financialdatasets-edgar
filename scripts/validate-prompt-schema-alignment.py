#!/usr/bin/env python3
"""Ensure answer-schema jq checks match the prompt's required output shape."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Top-level keys shown in flow.prompt "Required output shape" (not period_type).
PROMPT_TOP_LEVEL_KEYS = {
    "ticker",
    "fiscal_year",
    "fiscal_period",
    "report_type",
    "report_period",
    "source_kind",
    "income_statement",
    "balance_sheet",
    "cash_flow",
    "metrics",
    "missing_fields",
    "notes",
}

# Keys period-match compares against the fixture (subset of prompt metadata).
PERIOD_MATCH_KEYS = {
    "ticker",
    "fiscal_year",
    "fiscal_period",
    "report_period",
}

FORBIDDEN_TOP_LEVEL = {"period_type"}

JQ_FIELD_RE = re.compile(r"\(\.([a-z_]+)")


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def extract_answer_schema_jq(text: str) -> str:
    marker = "    - name: answer-schema\n"
    start = text.find(marker)
    if start < 0:
        fail("answer-schema test not found in experiment YAML")
    block = text[start : start + 8000]
    script_start = block.find("script: |\n")
    if script_start < 0:
        fail("answer-schema script block not found")
    script = block[script_start + len("script: |\n") :]
    end = script.find("\n    - name:")
    return script[:end] if end >= 0 else script


def jq_top_level_keys(jq_script: str) -> set[str]:
    keys: set[str] = set()
    for match in JQ_FIELD_RE.finditer(jq_script):
        field = match.group(1)
        if "." not in field:
            keys.add(field)
    return keys


def period_match_checks(text: str) -> set[str]:
    block_start = text.find("    - name: period-match\n")
    if block_start < 0:
        fail("period-match test not found")
    block = text[block_start : block_start + 4000]
    return {match.group(1) for match in re.finditer(r'got\.get\("([^"]+)"\)', block)}


def prompt_forbids_period_type(text: str) -> bool:
    prompt_start = text.find("flow:\n  prompt: |")
    if prompt_start < 0:
        fail("flow.prompt not found")
    prompt_block = text[prompt_start : prompt_start + 12000]
    return '"period_type"' not in prompt_block and "period_type" not in prompt_block.split(
        "tests:", 1
    )[0]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate experiment graders align with prompt output shape."
    )
    parser.add_argument(
        "--experiment",
        default=str(
            REPO_ROOT
            / "experiments"
            / "financialdatasets-vs-edgar-latest-reported-financials.yaml"
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    path = Path(args.experiment)
    text = path.read_text(encoding="utf-8")

    if not prompt_forbids_period_type(text):
        fail("prompt still mentions period_type; remove it or update this validator")

    jq_keys = jq_top_level_keys(extract_answer_schema_jq(text))
    missing = PROMPT_TOP_LEVEL_KEYS - jq_keys
    extra = jq_keys - PROMPT_TOP_LEVEL_KEYS
    forbidden = FORBIDDEN_TOP_LEVEL & jq_keys

    errors: list[str] = []
    if missing:
        errors.append(f"answer-schema jq missing prompt keys: {sorted(missing)}")
    if extra:
        errors.append(f"answer-schema jq has non-prompt keys: {sorted(extra)}")
    if forbidden:
        errors.append(f"answer-schema jq still requires: {sorted(forbidden)}")

    period_keys = period_match_checks(text)
    if period_keys & FORBIDDEN_TOP_LEVEL:
        errors.append("period-match still checks period_type")
    if "report_type" in period_keys and "report_type" not in PERIOD_MATCH_KEYS:
        errors.append("period-match checks report_type but fixture has no report_type")

    if errors:
        fail("prompt/schema alignment failed:\n" + "\n".join(errors))

    print(f"prompt/schema alignment passed: {path}")
    print(f"  answer-schema keys: {len(jq_keys)}")
    print(f"  period-match keys: {sorted(period_keys)}")


if __name__ == "__main__":
    main()
