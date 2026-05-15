#!/usr/bin/env python3
"""Inspect the SEC companyfacts rows used by the latest-quarter validator logic."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from datetime import date
from typing import Any


METADATA = {
    "aapl": {"ticker": "AAPL", "cik": "0000320193"},
    "msft": {"ticker": "MSFT", "cik": "0000789019"},
    "googl": {"ticker": "GOOGL", "cik": "0001652044"},
    "meta": {"ticker": "META", "cik": "0001326801"},
    "jnj": {"ticker": "JNJ", "cik": "0000200406"},
    "xom": {"ticker": "XOM", "cik": "0000034088"},
    "wmt": {"ticker": "WMT", "cik": "0000104169"},
    "tsla": {"ticker": "TSLA", "cik": "0001318605"},
    "cost": {"ticker": "COST", "cik": "0000909832"},
    "pg": {"ticker": "PG", "cik": "0000080424"},
}

TAG_CANDIDATES = {
    "revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
    ],
    "net_income": ["NetIncomeLoss"],
    "total_assets": ["Assets"],
    "operating_cash_flow": ["NetCashProvidedByUsedInOperatingActivities"],
    "capital_expenditures": ["PaymentsToAcquirePropertyPlantAndEquipment"],
}

QUARTERLY_FORMS = {"10-Q", "10-Q/A"}
ANNUAL_FORMS = {"10-K", "10-K/A"}
QUARTERLY_FPS = {"Q1", "Q2", "Q3"}
CUMULATIVE_FLOW_FIELDS = {"operating_cash_flow", "capital_expenditures"}
PRIOR_FISCAL_PERIOD = {"Q2": "Q1", "Q3": "Q2", "Q4": "Q3"}
FINANCE_LEASE_PRINCIPAL_TAG = "FinanceLeasePrincipalPayments"


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def load_companyfacts(cik: str) -> dict[str, Any]:
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "AXP eval validation contact@example.com"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def duration_days(row: dict[str, Any]) -> int:
    start = row.get("start")
    end = row.get("end")
    if not start or not end:
        return 0
    try:
        start_date = date.fromisoformat(start)
        end_date = date.fromisoformat(end)
    except ValueError:
        return 0
    return max((end_date - start_date).days, 0)


def matching_fact_rows(
    companyfacts: dict[str, Any],
    tags: list[str],
    fiscal_year: int,
    fiscal_period: str,
    report_period: str,
    forms: set[str],
) -> list[tuple[str, str, dict[str, Any]]]:
    facts = companyfacts.get("facts", {}).get("us-gaap", {})
    matches = []
    for tag in tags:
        units = facts.get(tag, {}).get("units", {}).get("USD", [])
        for row in units:
            if row.get("fy") != fiscal_year:
                continue
            if row.get("fp") != fiscal_period:
                continue
            if row.get("end") != report_period:
                continue
            if row.get("form") not in forms:
                continue
            matches.append((row.get("filed", ""), tag, row))
    return matches


def prior_ytd_fact_rows(
    companyfacts: dict[str, Any],
    tag: str,
    fiscal_year: int,
    prior_fiscal_period: str,
    report_period: str,
) -> list[tuple[str, str, dict[str, Any]]]:
    facts = companyfacts.get("facts", {}).get("us-gaap", {})
    matches = []
    for row in facts.get(tag, {}).get("units", {}).get("USD", []):
        if row.get("fy") != fiscal_year:
            continue
        if row.get("fp") != prior_fiscal_period:
            continue
        if row.get("form") not in QUARTERLY_FORMS:
            continue
        if not row.get("end") or row.get("end") >= report_period:
            continue
        matches.append((row.get("filed", ""), tag, row))
    return matches


def choose_fact_row(
    companyfacts: dict[str, Any],
    tags: list[str],
    fiscal_year: int,
    fiscal_period: str,
    report_period: str,
    duration_preference: str,
    forms: set[str],
) -> tuple[str, dict[str, Any]]:
    matches = matching_fact_rows(
        companyfacts,
        tags,
        fiscal_year,
        fiscal_period,
        report_period,
        forms,
    )
    if not matches:
        fail(f"missing SEC fact for {tags} @ {report_period}")

    multiplier = 1 if duration_preference == "longest" else -1
    matches.sort(
        key=lambda item: (
            item[0],
            multiplier * duration_days(item[2]),
            item[1],
        ),
        reverse=True,
    )
    _, tag, row = matches[0]
    return tag, row


def choose_prior_ytd_fact_row(
    companyfacts: dict[str, Any],
    tag: str,
    fiscal_year: int,
    prior_fiscal_period: str,
    report_period: str,
) -> dict[str, Any]:
    matches = prior_ytd_fact_rows(
        companyfacts,
        tag,
        fiscal_year,
        prior_fiscal_period,
        report_period,
    )
    if not matches:
        fail(f"missing {prior_fiscal_period} YTD SEC fact for {tag} before {report_period}")

    matches.sort(
        key=lambda item: (
            item[2].get("end", ""),
            item[0],
            duration_days(item[2]),
        ),
        reverse=True,
    )
    return matches[0][2]


def derived_quarter_value(
    companyfacts: dict[str, Any],
    field: str,
    fiscal_year: int,
    fiscal_period: str,
    report_period: str,
    selection_mode: str,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    source_fp = "FY" if selection_mode == "q4_from_fy" else fiscal_period
    source_forms = ANNUAL_FORMS if selection_mode == "q4_from_fy" else QUARTERLY_FORMS
    source_tag, source_row = choose_fact_row(
        companyfacts,
        TAG_CANDIDATES[field],
        fiscal_year,
        source_fp,
        report_period,
        "longest",
        source_forms,
    )
    prior_row = choose_prior_ytd_fact_row(
        companyfacts,
        source_tag,
        fiscal_year,
        PRIOR_FISCAL_PERIOD[fiscal_period],
        report_period,
    )
    return source_tag, source_row, prior_row


def selected_fact_value(
    companyfacts: dict[str, Any],
    field: str,
    fiscal_year: int,
    fiscal_period: str,
    report_period: str,
    selection_mode: str,
) -> int | float:
    if selection_mode == "q4_from_fy" and field in {"revenue", "net_income"}:
        _, source_row, prior_row = derived_quarter_value(
            companyfacts,
            field,
            fiscal_year,
            fiscal_period,
            report_period,
            selection_mode,
        )
        return source_row.get("val") - prior_row.get("val")

    if field in CUMULATIVE_FLOW_FIELDS and fiscal_period in PRIOR_FISCAL_PERIOD:
        _, source_row, prior_row = derived_quarter_value(
            companyfacts,
            field,
            fiscal_year,
            fiscal_period,
            report_period,
            selection_mode,
        )
        return source_row.get("val") - prior_row.get("val")

    if selection_mode == "q4_from_fy":
        _, row = choose_fact_row(
            companyfacts,
            TAG_CANDIDATES[field],
            fiscal_year,
            "FY",
            report_period,
            "longest",
            ANNUAL_FORMS,
        )
        return row.get("val")

    duration_preference = (
        "shortest" if field in {"revenue", "net_income"} else "longest"
    )
    _, row = choose_fact_row(
        companyfacts,
        TAG_CANDIDATES[field],
        fiscal_year,
        fiscal_period,
        report_period,
        duration_preference,
        QUARTERLY_FORMS,
    )
    return row.get("val")


def latest_report_period(companyfacts: dict[str, Any]) -> dict[str, Any]:
    facts = companyfacts.get("facts", {}).get("us-gaap", {})
    candidates = []
    for tag in TAG_CANDIDATES["revenue"]:
        for row in facts.get(tag, {}).get("units", {}).get("USD", []):
            fiscal_year = row.get("fy")
            fiscal_period = row.get("fp")
            report_period = row.get("end")
            filed = row.get("filed", "")
            if not isinstance(fiscal_year, int):
                continue
            if not report_period:
                continue
            if row.get("form") in QUARTERLY_FORMS and fiscal_period in QUARTERLY_FPS:
                candidates.append((filed, report_period, fiscal_year, fiscal_period, "direct"))
            if row.get("form") in ANNUAL_FORMS and fiscal_period == "FY":
                candidates.append((filed, report_period, fiscal_year, "Q4", "q4_from_fy"))

    candidates = sorted(set(candidates), reverse=True)
    for _, report_period, fiscal_year, fiscal_period, selection_mode in candidates:
        try:
            for field in TAG_CANDIDATES:
                selected_fact_value(
                    companyfacts,
                    field,
                    fiscal_year,
                    fiscal_period,
                    report_period,
                    selection_mode,
                )
        except SystemExit:
            continue
        return {
            "fiscal_year": fiscal_year,
            "fiscal_period": fiscal_period,
            "report_period": report_period,
            "selection_mode": selection_mode,
        }

    fail("could not find a latest SEC quarterly reporting period with all required facts")


def selected_fact(
    companyfacts: dict[str, Any],
    field: str,
    fiscal_year: int,
    fiscal_period: str,
    report_period: str,
    selection_mode: str,
) -> dict[str, Any]:
    if selection_mode == "q4_from_fy" and field in {"revenue", "net_income"}:
        source_tag, source_row, prior_row = derived_quarter_value(
            companyfacts,
            field,
            fiscal_year,
            fiscal_period,
            report_period,
            selection_mode,
        )
        return {
            "tag": source_tag,
            "value": source_row.get("val") - prior_row.get("val"),
            "derivation": "FY 10-K value minus prior 10-Q year-to-date value",
            "source_fact": {
                "value": source_row.get("val"),
                "form": source_row.get("form"),
                "filed": source_row.get("filed"),
                "accession": source_row.get("accn"),
                "fy": source_row.get("fy"),
                "fp": source_row.get("fp"),
                "start": source_row.get("start"),
                "end": source_row.get("end"),
                "duration_days": duration_days(source_row),
            },
            "prior_ytd_fact": {
                "value": prior_row.get("val"),
                "form": prior_row.get("form"),
                "filed": prior_row.get("filed"),
                "accession": prior_row.get("accn"),
                "fy": prior_row.get("fy"),
                "fp": prior_row.get("fp"),
                "start": prior_row.get("start"),
                "end": prior_row.get("end"),
                "duration_days": duration_days(prior_row),
            },
        }

    if field in CUMULATIVE_FLOW_FIELDS and fiscal_period in PRIOR_FISCAL_PERIOD:
        source_tag, source_row, prior_row = derived_quarter_value(
            companyfacts,
            field,
            fiscal_year,
            fiscal_period,
            report_period,
            selection_mode,
        )
        source_name = "FY 10-K" if selection_mode == "q4_from_fy" else f"{fiscal_period} 10-Q"
        return {
            "tag": source_tag,
            "value": source_row.get("val") - prior_row.get("val"),
            "derivation": f"{source_name} year-to-date value minus prior 10-Q year-to-date value",
            "source_fact": {
                "value": source_row.get("val"),
                "form": source_row.get("form"),
                "filed": source_row.get("filed"),
                "accession": source_row.get("accn"),
                "fy": source_row.get("fy"),
                "fp": source_row.get("fp"),
                "start": source_row.get("start"),
                "end": source_row.get("end"),
                "duration_days": duration_days(source_row),
            },
            "prior_ytd_fact": {
                "value": prior_row.get("val"),
                "form": prior_row.get("form"),
                "filed": prior_row.get("filed"),
                "accession": prior_row.get("accn"),
                "fy": prior_row.get("fy"),
                "fp": prior_row.get("fp"),
                "start": prior_row.get("start"),
                "end": prior_row.get("end"),
                "duration_days": duration_days(prior_row),
            },
        }

    if selection_mode == "q4_from_fy":
        tag, row = choose_fact_row(
            companyfacts,
            TAG_CANDIDATES[field],
            fiscal_year,
            "FY",
            report_period,
            "longest",
            ANNUAL_FORMS,
        )
        duration_preference = "longest"
    else:
        duration_preference = (
            "shortest" if field in {"revenue", "net_income"} else "longest"
        )
        tag, row = choose_fact_row(
            companyfacts,
            TAG_CANDIDATES[field],
            fiscal_year,
            fiscal_period,
            report_period,
            duration_preference,
            QUARTERLY_FORMS,
        )
    return {
        "tag": tag,
        "value": row.get("val"),
        "form": row.get("form"),
        "filed": row.get("filed"),
        "accession": row.get("accn"),
        "fy": row.get("fy"),
        "fp": row.get("fp"),
        "start": row.get("start"),
        "end": row.get("end"),
        "duration_days": duration_days(row),
        "duration_preference": duration_preference,
    }


def optional_cumulative_tag_fact(
    companyfacts: dict[str, Any],
    tag: str,
    fiscal_year: int,
    fiscal_period: str,
    report_period: str,
    selection_mode: str,
) -> dict[str, Any] | None:
    def choose_optional(matches: list[tuple[str, str, dict[str, Any]]]) -> dict[str, Any] | None:
        if not matches:
            return None
        matches.sort(
            key=lambda item: (
                item[0],
                duration_days(item[2]),
                item[1],
            ),
            reverse=True,
        )
        return matches[0][2]

    if fiscal_period in PRIOR_FISCAL_PERIOD:
        source_fp = "FY" if selection_mode == "q4_from_fy" else fiscal_period
        source_forms = ANNUAL_FORMS if selection_mode == "q4_from_fy" else QUARTERLY_FORMS
        source_row = choose_optional(
            matching_fact_rows(
                companyfacts,
                [tag],
                fiscal_year,
                source_fp,
                report_period,
                source_forms,
            )
        )
        prior_row = choose_optional(
            prior_ytd_fact_rows(
                companyfacts,
                tag,
                fiscal_year,
                PRIOR_FISCAL_PERIOD[fiscal_period],
                report_period,
            )
        )
        if not source_row or not prior_row:
            return None
        source_name = "FY 10-K" if selection_mode == "q4_from_fy" else f"{fiscal_period} 10-Q"
        return {
            "tag": tag,
            "value": source_row.get("val") - prior_row.get("val"),
            "derivation": f"{source_name} year-to-date value minus prior 10-Q year-to-date value",
            "source_fact": {
                "value": source_row.get("val"),
                "form": source_row.get("form"),
                "filed": source_row.get("filed"),
                "accession": source_row.get("accn"),
                "fy": source_row.get("fy"),
                "fp": source_row.get("fp"),
                "start": source_row.get("start"),
                "end": source_row.get("end"),
                "duration_days": duration_days(source_row),
            },
            "prior_ytd_fact": {
                "value": prior_row.get("val"),
                "form": prior_row.get("form"),
                "filed": prior_row.get("filed"),
                "accession": prior_row.get("accn"),
                "fy": prior_row.get("fy"),
                "fp": prior_row.get("fp"),
                "start": prior_row.get("start"),
                "end": prior_row.get("end"),
                "duration_days": duration_days(prior_row),
            },
        }

    row = choose_optional(
        matching_fact_rows(
            companyfacts,
            [tag],
            fiscal_year,
            fiscal_period,
            report_period,
            QUARTERLY_FORMS,
        )
    )
    if not row:
        return None
    return {
        "tag": tag,
        "value": row.get("val"),
        "form": row.get("form"),
        "filed": row.get("filed"),
        "accession": row.get("accn"),
        "fy": row.get("fy"),
        "fp": row.get("fp"),
        "start": row.get("start"),
        "end": row.get("end"),
        "duration_days": duration_days(row),
        "duration_preference": "longest",
    }


def inspect_ticker(ticker_key: str) -> dict[str, Any]:
    meta = METADATA[ticker_key]
    companyfacts = load_companyfacts(meta["cik"])
    latest = latest_report_period(companyfacts)
    fields = {
        field: selected_fact(
            companyfacts,
            field,
            latest["fiscal_year"],
            latest["fiscal_period"],
            latest["report_period"],
            latest["selection_mode"],
        )
        for field in TAG_CANDIDATES
    }
    finance_lease_principal = optional_cumulative_tag_fact(
        companyfacts,
        FINANCE_LEASE_PRINCIPAL_TAG,
        latest["fiscal_year"],
        latest["fiscal_period"],
        latest["report_period"],
        latest["selection_mode"],
    )
    if finance_lease_principal:
        property_and_equipment = fields["capital_expenditures"]
        fields["capital_expenditures"] = {
            "tag": f"{property_and_equipment['tag']}+{FINANCE_LEASE_PRINCIPAL_TAG}",
            "value": property_and_equipment["value"] + finance_lease_principal["value"],
            "derivation": "purchases of property and equipment plus principal payments on finance leases",
            "components": {
                "property_and_equipment": property_and_equipment,
                "finance_lease_principal_payments": finance_lease_principal,
            },
        }
    financials = {field: detail["value"] for field, detail in fields.items()}
    financials["free_cash_flow"] = (
        financials["operating_cash_flow"] - financials["capital_expenditures"]
    )
    ratios = {
        "net_margin": financials["net_income"] / financials["revenue"],
        "free_cash_flow_margin": financials["free_cash_flow"] / financials["revenue"],
    }
    return {
        "ticker": meta["ticker"],
        "cik": meta["cik"],
        **latest,
        "companyfacts_url": f"https://data.sec.gov/api/xbrl/companyfacts/CIK{meta['cik']}.json",
        "selected_facts": fields,
        "financials_usd": financials,
        "ratios": ratios,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Print the latest SEC financial rows selected by the validator logic."
    )
    parser.add_argument(
        "tickers",
        nargs="*",
        help="Ticker keys to inspect. Defaults to all scenario tickers.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    ticker_keys = [ticker.lower() for ticker in args.tickers] or sorted(METADATA)
    unknown = [ticker for ticker in ticker_keys if ticker not in METADATA]
    if unknown:
        fail(f"unknown ticker(s): {', '.join(unknown)}")

    results = [inspect_ticker(ticker) for ticker in ticker_keys]
    print(json.dumps(results, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
