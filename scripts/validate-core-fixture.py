#!/usr/bin/env python3
"""Validate the core reporting-period fixture and metric policies."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from fixture_paths import resolve_core_fixture_path
from ticker_registry import ticker_keys as registry_ticker_keys

DEFAULT_POLICIES_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "metric-policies.json"


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def iter_policy_metrics(policies: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    metrics = []
    for section, section_metrics in policies["metrics"].items():
        for key, policy in section_metrics.items():
            metrics.append((section, key, policy))
    return metrics


def grading_config(policies: dict[str, Any]) -> dict[str, Any]:
    return policies.get("grading", policies.get("score_policy", {}))


def validate_policies(policies: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not policies.get("version"):
        errors.append("metric policies missing version")
    grading = grading_config(policies)
    for field in ("min_correct", "relative_tolerance", "absolute_tolerance_usd"):
        if field not in grading:
            errors.append(f"metric policies grading missing {field}")
    for section, key, policy in iter_policy_metrics(policies):
        path = f"{section}.{key}"
        method = policy.get("method")
        if not method:
            errors.append(f"{path} missing method")
            continue
        if method in {"direct", "cumulative_if_needed", "current_period_from_cumulative_if_needed"}:
            if not policy.get("tags") and not policy.get("candidate_tags"):
                errors.append(f"{path} missing tags")
        if method == "filing_dimensional":
            segment = policy.get("segment", {})
            if not policy.get("tag") and not policy.get("tags"):
                errors.append(f"{path} filing_dimensional missing tag or tags")
            if "dimension" not in segment or "member" not in segment:
                errors.append(f"{path} filing_dimensional missing segment.dimension/member")
        if method == "formula" and "formula" not in policy:
            errors.append(f"{path} formula method missing formula")
    return errors


def validate_fixture(fixture: dict[str, Any], policies: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    as_of_date = fixture.get("as_of_date") or fixture.get("metadata", {}).get("as_of_date")
    if not as_of_date:
        errors.append("fixture missing as_of_date")

    grading = fixture.get("grading", grading_config(policies))
    for field in ("min_correct", "relative_tolerance", "absolute_tolerance_usd"):
        if field not in grading:
            errors.append(f"fixture grading missing {field}")

    expected = fixture.get("expected_by_ticker", {})
    if not expected:
        errors.append("fixture missing expected_by_ticker")
        return errors

    required = registry_ticker_keys()
    if sorted(expected) != required:
        errors.append(
            f"fixture tickers mismatch: expected {required}, got {sorted(expected)}"
        )

    for ticker_key in required:
        entry = expected.get(ticker_key)
        if not entry:
            errors.append(f"missing ticker entry: {ticker_key}")
            continue

        period = entry.get("period", {})
        for field in ("fiscal_year", "fiscal_period", "report_period", "period_type"):
            if field not in period:
                errors.append(f"{ticker_key}.period missing {field}")

        fiscal_period = period.get("fiscal_period")
        expected_period_type = "year_end" if fiscal_period == "Q4" else "interim"
        if period.get("period_type") != expected_period_type:
            errors.append(
                f"{ticker_key}.period.period_type mismatch for fiscal_period {fiscal_period}"
            )

        core_values = entry.get("core_values", {})
        if not core_values:
            errors.append(f"{ticker_key} missing core_values")
            continue

        for section, key, _ in iter_policy_metrics(policies):
            if core_values.get(section, {}).get(key) is None:
                errors.append(f"{ticker_key}.core_values.{section}.{key} is null")

        cash_flow = core_values.get("cash_flow", {})
        ocf = cash_flow.get("operating_cash_flow")
        capex = cash_flow.get("capital_expenditures")
        fcf = cash_flow.get("free_cash_flow")
        if ocf is not None and capex is not None and fcf is not None and fcf != ocf - capex:
            errors.append(
                f"{ticker_key} free_cash_flow != operating_cash_flow - capital_expenditures"
            )

    return errors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate core fixture and metric policies.")
    parser.add_argument(
        "--fixture",
        help="Path to latest-reporting-period-core.json (default: resolved from fixtures/)",
    )
    parser.add_argument(
        "--policies",
        default=str(DEFAULT_POLICIES_PATH),
        help="Path to metric-policies.json",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    fixture_path = Path(args.fixture) if args.fixture else resolve_core_fixture_path()
    fixture = load_json(fixture_path)
    policies = load_json(Path(args.policies))

    errors = []
    errors.extend(validate_policies(policies))
    errors.extend(validate_fixture(fixture, policies))
    if errors:
        fail("fixture validation failed:\n" + "\n".join(errors))
    print(f"fixture validation passed: {fixture_path}")


if __name__ == "__main__":
    main()
