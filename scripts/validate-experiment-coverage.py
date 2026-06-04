#!/usr/bin/env python3
"""Ensure experiment variant matrix covers every ticker in fixtures/tickers.json."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from ticker_registry import ticker_keys

REPO_ROOT = Path(__file__).resolve().parents[1]
VARIANT_ID_LINE = re.compile(r"^\s+-\s+id:\s+(\S+)\s*$")


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def variant_ids(experiment_path: Path) -> set[str]:
    ids: set[str] = set()
    for line in experiment_path.read_text(encoding="utf-8").splitlines():
        match = VARIANT_ID_LINE.match(line)
        if match:
            ids.add(match.group(1))
    return ids


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate experiment YAML variants match fixtures/tickers.json."
    )
    parser.add_argument(
        "--experiment",
        default=str(
            REPO_ROOT
            / "experiments"
            / "financialdatasets-vs-edgar-latest-reported-financials.yaml"
        ),
        help="Experiment YAML path",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    experiment_path = Path(args.experiment)
    if not experiment_path.is_file():
        fail(f"experiment not found: {experiment_path}")

    registry = ticker_keys()
    ids = variant_ids(experiment_path)
    errors: list[str] = []

    for key in registry:
        for suffix in ("financialdatasets-rest", "sec-edgar"):
            expected = f"{key}-{suffix}"
            if expected not in ids:
                errors.append(f"missing variant id: {expected}")

    for variant_id in sorted(ids):
        if not (
            variant_id.endswith("-financialdatasets-rest")
            or variant_id.endswith("-sec-edgar")
        ):
            continue
        key = variant_id.split("-", 1)[0]
        if key not in registry:
            errors.append(f"unknown ticker in experiment matrix: {key} ({variant_id})")

    if errors:
        fail("experiment coverage failed:\n" + "\n".join(errors))
    print(f"experiment coverage passed: {experiment_path}")


if __name__ == "__main__":
    main()
