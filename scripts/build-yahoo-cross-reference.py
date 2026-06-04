#!/usr/bin/env python3
"""Build fixtures/yahoo-cross-reference.json from Yahoo fundamentals timeseries API."""

from __future__ import annotations

import json
import sys
import urllib.request
from datetime import date, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = REPO_ROOT / "fixtures" / "latest-reporting-period-core.json"
TICKERS_PATH = REPO_ROOT / "fixtures" / "tickers.json"
OUTPUT_PATH = REPO_ROOT / "fixtures" / "yahoo-cross-reference.json"

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
MAX_PERIOD_DAY_GAP = 14

YAHOO_TYPES = {
    "income_statement.revenue": "quarterlyTotalRevenue",
    "income_statement.net_income": "quarterlyNetIncomeCommonStockholders",
    "balance_sheet.total_assets": "quarterlyTotalAssets",
    "cash_flow.operating_cash_flow": "quarterlyOperatingCashFlow",
    "cash_flow.capital_expenditures": "quarterlyCapitalExpenditure",
    "cash_flow.free_cash_flow": "quarterlyFreeCashFlow",
}


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def parse_date(value: str) -> date:
    return date.fromisoformat(value)


def within_tolerance(fixture_val: int, yahoo_val: int, rel_tol: float, abs_tol: int) -> bool:
    delta = fixture_val - yahoo_val
    limit = max(abs_tol, rel_tol * abs(yahoo_val))
    return abs(delta) <= limit


def fetch_yahoo_series(symbol: str) -> dict[str, dict[str, float]]:
    types = ",".join(YAHOO_TYPES.values())
    url = (
        "https://query1.finance.yahoo.com/ws/fundamentals-timeseries/v1/"
        f"finance/timeseries/{symbol}?symbol={symbol}&type={types}"
        "&period1=0&period2=2000000000"
    )
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.load(resp)

    out: dict[str, dict[str, float]] = {}
    for block in data.get("timeseries", {}).get("result", []):
        if not isinstance(block, dict):
            continue
        for key, series in block.items():
            if key in ("meta", "timestamp") or not isinstance(series, list):
                continue
            for item in series:
                if not isinstance(item, dict):
                    continue
                as_of = item.get("asOfDate")
                raw = item.get("reportedValue", {}).get("raw")
                if as_of and raw is not None:
                    out.setdefault(key, {})[as_of] = raw
    return out


def pick_yahoo_period(
    series_by_type: dict[str, dict[str, float]],
    report_period: str,
) -> tuple[str | None, str | None]:
    """Return (yahoo_as_of_date, note) for best column match."""
    all_dates: set[str] = set()
    for rows in series_by_type.values():
        all_dates.update(rows)

    if report_period in all_dates:
        return report_period, None

    target = parse_date(report_period)
    candidates = []
    for candidate in all_dates:
        gap = abs((parse_date(candidate) - target).days)
        if gap <= MAX_PERIOD_DAY_GAP:
            candidates.append((gap, candidate))

    if not candidates:
        latest = sorted(all_dates, reverse=True)
        latest_preview = latest[:3]
        return None, (
            f"No Yahoo row within {MAX_PERIOD_DAY_GAP} days of {report_period}; "
            f"latest available: {latest_preview}"
        )

    candidates.sort()
    gap, chosen = candidates[0]
    if gap == 0:
        return chosen, None
    return chosen, (
        f"Yahoo column {chosen} used ({gap} day(s) from report_period {report_period})"
    )


def normalize_value(metric_path: str, raw: float) -> int:
    if metric_path == "cash_flow.capital_expenditures":
        return int(abs(raw))
    return int(raw)


def main() -> None:
    fixture = load_json(FIXTURE_PATH)
    tickers = load_json(TICKERS_PATH)["tickers"]
    grading = fixture["grading"]
    rel_tol = grading["relative_tolerance"]
    abs_tol = int(grading["absolute_tolerance_usd"])

    result: dict = {
        "as_of_date": fixture["as_of_date"],
        "yahoo_checked_at": date.today().isoformat(),
        "yahoo_source": (
            "query1.finance.yahoo.com/ws/fundamentals-timeseries/v1 "
            "(quarterly metrics; CapEx stored as absolute spend)"
        ),
        "period_match_max_day_gap": MAX_PERIOD_DAY_GAP,
        "grading": grading,
        "tickers": {},
        "summary": {
            "match": 0,
            "mismatch": 0,
            "period_adjusted": 0,
            "missing_yahoo": 0,
            "errors": 0,
        },
    }

    for ticker_key in sorted(tickers):
        meta = tickers[ticker_key]
        symbol = meta["symbol"]
        entry = fixture["expected_by_ticker"][ticker_key]
        period = entry["period"]
        report_period = period["report_period"]
        core = entry["core_values"]

        ticker_row: dict = {
            "symbol": symbol,
            "report_period": report_period,
            "yahoo_as_of_date": None,
            "period": period,
            "yahoo_url": f"https://finance.yahoo.com/quote/{symbol}/financials/?period=quarterly",
            "yahoo_balance_sheet_url": (
                f"https://finance.yahoo.com/quote/{symbol}/balance-sheet/?period=quarterly"
            ),
            "yahoo_cash_flow_url": (
                f"https://finance.yahoo.com/quote/{symbol}/cash-flow/?period=quarterly"
            ),
            "metrics": {},
            "notes": [],
        }

        try:
            series = fetch_yahoo_series(symbol)
        except urllib.error.URLError as exc:
            ticker_row["error"] = str(exc)
            result["summary"]["errors"] += 1
            result["tickers"][ticker_key] = ticker_row
            continue

        yahoo_period, period_note = pick_yahoo_period(series, report_period)
        if period_note:
            ticker_row["notes"].append(period_note)
            if yahoo_period and yahoo_period != report_period:
                result["summary"]["period_adjusted"] += 1
        ticker_row["yahoo_as_of_date"] = yahoo_period

        for metric_path, yahoo_type in YAHOO_TYPES.items():
            section, key = metric_path.split(".", 1)
            fixture_val = core.get(section, {}).get(key)

            if yahoo_period is None:
                payload = {
                    "yahoo": None,
                    "fixture": fixture_val,
                    "match": None,
                    "delta_usd": None,
                    "notes": period_note,
                }
                result["summary"]["missing_yahoo"] += 1
            else:
                raw = series.get(yahoo_type, {}).get(yahoo_period)
                if raw is None:
                    payload = {
                        "yahoo": None,
                        "fixture": fixture_val,
                        "match": None,
                        "delta_usd": None,
                        "notes": f"Missing {yahoo_type} for {yahoo_period}",
                    }
                    result["summary"]["missing_yahoo"] += 1
                else:
                    yahoo_val = normalize_value(metric_path, raw)
                    match = within_tolerance(fixture_val, yahoo_val, rel_tol, abs_tol)
                    payload = {
                        "yahoo": yahoo_val,
                        "fixture": fixture_val,
                        "match": match,
                        "delta_usd": fixture_val - yahoo_val,
                    }
                    if not match:
                        payload["notes"] = "Outside grading tolerance"
                        result["summary"]["mismatch"] += 1
                    else:
                        result["summary"]["match"] += 1

            ticker_row["metrics"][metric_path] = payload

        ocf = ticker_row["metrics"]["cash_flow.operating_cash_flow"].get("yahoo")
        capex = ticker_row["metrics"]["cash_flow.capital_expenditures"].get("yahoo")
        fcf = ticker_row["metrics"]["cash_flow.free_cash_flow"].get("yahoo")
        if ocf is not None and capex is not None and fcf is not None:
            calc = ocf - capex
            if calc != fcf:
                ticker_row["notes"].append(
                    f"Yahoo FCF identity: operating_cash_flow - capex = {calc} != free_cash_flow {fcf}"
                )

        result["tickers"][ticker_key] = ticker_row

    OUTPUT_PATH.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUTPUT_PATH}")
    print(json.dumps(result["summary"], indent=2))

    if result["summary"]["mismatch"] or result["summary"]["missing_yahoo"]:
        for ticker_key, row in result["tickers"].items():
            bad = [
                path
                for path, metric in row.get("metrics", {}).items()
                if metric.get("match") is False or metric.get("yahoo") is None
            ]
            if bad:
                print(f"  {ticker_key}: {bad}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
