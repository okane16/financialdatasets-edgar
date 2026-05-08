#!/usr/bin/env python3
"""Compare paired Financial Datasets and SEC EDGAR variants for one AXP run."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import urlparse


FIELD_NAMES = [
    "revenue",
    "net_income",
    "total_assets",
    "operating_cash_flow",
    "capital_expenditures",
    "free_cash_flow",
]

RATIO_NAMES = ["net_margin", "free_cash_flow_margin"]

FINANCIAL_DATASETS_PRICES = {
    "/financials": 0.10,
    "/financials/income-statements": 0.04,
    "/financials/balance-sheets": 0.04,
    "/financials/cash-flow-statements": 0.04,
    "/filings": 0.02,
}


def load_json(path: Path) -> object:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def variant_ticker(variant_id: str) -> str:
    return variant_id.split("-", 1)[0]


def source_kind(variant_id: str) -> str:
    if variant_id.endswith("-financialdatasets-rest"):
        return "financialdatasets-rest"
    if variant_id.endswith("-sec-edgar"):
        return "sec-edgar"
    fail(f"unsupported variant id: {variant_id}")
    raise AssertionError("unreachable")


def normalize_endpoint(url: str) -> str | None:
    parsed = urlparse(url)
    if parsed.netloc != "api.financialdatasets.ai":
        return None
    return parsed.path.rstrip("/") or "/"


def financialdatasets_api_cost(answer: dict) -> float:
    total = 0.0
    for url in answer.get("source_urls", []):
        endpoint = normalize_endpoint(url)
        if endpoint is None:
            continue
        total += FINANCIAL_DATASETS_PRICES.get(endpoint, 0.0)
    return total


def assert_close(ticker: str, field: str, left: float, right: float, errors: list[str]) -> None:
    tolerance = max(abs(right) * 0.002, 5_000_000)
    if abs(left - right) > tolerance:
        errors.append(
            f"{ticker}: {field} mismatch: financialdatasets={left} edgar={right} tolerance={tolerance}"
        )


def validate_answer_source(variant_id: str, answer: dict, errors: list[str]) -> None:
    expected = source_kind(variant_id)
    actual = answer.get("source_kind")
    if actual != expected:
        errors.append(f"{variant_id}: source_kind {actual!r} != {expected!r}")

    urls = " ".join(answer.get("source_urls", [])).lower()
    if expected == "financialdatasets-rest":
        if "api.financialdatasets.ai" not in urls:
            errors.append(f"{variant_id}: missing api.financialdatasets.ai source URL")
    else:
        if "financialdatasets.ai" in urls:
            errors.append(f"{variant_id}: SEC EDGAR variant references financialdatasets.ai")
        if "sec.gov" not in urls and "data.sec.gov" not in urls:
            errors.append(f"{variant_id}: missing sec.gov/data.sec.gov source URL")


def load_variant(run_root: Path, variant_id: str) -> dict:
    result_dir = run_root / "results" / variant_id
    answer_path = result_dir / "workspace" / "answer.json"
    summary_path = result_dir / "run-summary.json"
    if not answer_path.is_file():
        fail(f"missing answer.json for {variant_id}: {answer_path}")
    if not summary_path.is_file():
        fail(f"missing run-summary.json for {variant_id}: {summary_path}")
    return {
        "answer": load_json(answer_path),
        "summary": load_json(summary_path),
    }


def compare_pair(ticker: str, fd: dict, edgar: dict) -> tuple[dict, list[str]]:
    errors: list[str] = []
    fd_answer = fd["answer"]
    edgar_answer = edgar["answer"]

    for key in ["ticker", "report_period"]:
        if fd_answer.get(key) != edgar_answer.get(key):
            errors.append(
                f"{ticker}: {key} mismatch: financialdatasets={fd_answer.get(key)!r} edgar={edgar_answer.get(key)!r}"
            )

    fd_financials = fd_answer.get("financials_usd", {})
    edgar_financials = edgar_answer.get("financials_usd", {})
    for field in FIELD_NAMES:
        if field not in fd_financials or field not in edgar_financials:
            errors.append(f"{ticker}: missing field {field}")
            continue
        assert_close(ticker, field, fd_financials[field], edgar_financials[field], errors)

    fd_ratios = fd_answer.get("ratios", {})
    edgar_ratios = edgar_answer.get("ratios", {})
    for ratio in RATIO_NAMES:
        if ratio not in fd_ratios or ratio not in edgar_ratios:
            errors.append(f"{ticker}: missing ratio {ratio}")
            continue
        if abs(fd_ratios[ratio] - edgar_ratios[ratio]) > 0.002:
            errors.append(
                f"{ticker}: {ratio} mismatch: financialdatasets={fd_ratios[ratio]} edgar={edgar_ratios[ratio]}"
            )

    fd_token_cost = fd["summary"]["agent"]["cost_usd"]
    edgar_token_cost = edgar["summary"]["agent"]["cost_usd"]
    fd_api_cost = financialdatasets_api_cost(fd_answer)
    edgar_api_cost = 0.0
    fd_total_cost = fd_token_cost + fd_api_cost
    edgar_total_cost = edgar_token_cost + edgar_api_cost

    row = {
        "ticker": ticker.upper(),
        "report_period": fd_answer.get("report_period"),
        "matches": not errors,
        "financialdatasets": {
            "status": fd["summary"]["status"],
            "token_cost_usd": fd_token_cost,
            "api_cost_usd": fd_api_cost,
            "total_cost_usd": fd_total_cost,
            "duration_seconds": fd["summary"]["duration_seconds"],
        },
        "edgar": {
            "status": edgar["summary"]["status"],
            "token_cost_usd": edgar_token_cost,
            "api_cost_usd": edgar_api_cost,
            "total_cost_usd": edgar_total_cost,
            "duration_seconds": edgar["summary"]["duration_seconds"],
        },
        "delta": {
            "total_cost_usd": fd_total_cost - edgar_total_cost,
            "duration_seconds": fd["summary"]["duration_seconds"] - edgar["summary"]["duration_seconds"],
        },
        "errors": errors,
    }
    return row, errors


def main() -> int:
    if len(sys.argv) != 2:
        fail("usage: validate-financialdatasets-edgar-run.py <run-group-dir>")

    run_root = Path(sys.argv[1]).resolve()
    index_path = run_root / "index.json"
    if not index_path.is_file():
        fail(f"missing run group index: {index_path}")

    index = load_json(index_path)
    variants = index.get("variants", {})
    groups: dict[str, dict[str, str]] = {}
    errors: list[str] = []

    for variant_id in variants:
        ticker = variant_ticker(variant_id)
        groups.setdefault(ticker, {})[source_kind(variant_id)] = variant_id

    rows = []
    for ticker in sorted(groups):
        pair = groups[ticker]
        fd_variant = pair.get("financialdatasets-rest")
        edgar_variant = pair.get("sec-edgar")
        if not fd_variant or not edgar_variant:
            errors.append(f"{ticker}: missing paired variant")
            continue

        fd = load_variant(run_root, fd_variant)
        edgar = load_variant(run_root, edgar_variant)
        validate_answer_source(fd_variant, fd["answer"], errors)
        validate_answer_source(edgar_variant, edgar["answer"], errors)
        row, pair_errors = compare_pair(ticker, fd, edgar)
        errors.extend(pair_errors)
        rows.append(row)

    summary = {
        "run_group_id": index.get("run_group_id"),
        "pairs": len(rows),
        "matching_pairs": sum(1 for row in rows if row["matches"]),
        "financialdatasets_total_cost_usd": sum(row["financialdatasets"]["total_cost_usd"] for row in rows),
        "edgar_total_cost_usd": sum(row["edgar"]["total_cost_usd"] for row in rows),
        "financialdatasets_total_duration_seconds": sum(row["financialdatasets"]["duration_seconds"] for row in rows),
        "edgar_total_duration_seconds": sum(row["edgar"]["duration_seconds"] for row in rows),
        "errors": errors,
    }

    report = {"summary": summary, "pairs": rows}
    print(json.dumps(report, indent=2, sort_keys=True))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
