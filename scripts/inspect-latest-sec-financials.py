#!/usr/bin/env python3
"""Generate deterministic SEC companyfacts fixtures for core financial evals."""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any

from ticker_registry import metadata_dict, ticker_keys as registry_ticker_keys

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICIES_PATH = REPO_ROOT / "fixtures" / "metric-policies.json"
DEFAULT_AS_OF_DATE = "2026-06-03"


def metadata() -> dict[str, dict[str, str]]:
    return metadata_dict()

QUARTERLY_FORMS = {"10-Q", "10-Q/A"}
ANNUAL_FORMS = {"10-K", "10-K/A"}
QUARTERLY_FPS = {"Q1", "Q2", "Q3"}
PRIOR_FISCAL_PERIOD = {"Q2": "Q1", "Q3": "Q2", "Q4": "Q3"}
CUMULATIVE_METHOD = "cumulative_if_needed"
FILING_HTML_CACHE: dict[tuple[str, str], str] = {}


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def parse_iso_date(value: str) -> date:
    return date.fromisoformat(value)


def filed_on_or_before(row: dict[str, Any], as_of_date: date) -> bool:
    filed = row.get("filed")
    if not filed:
        return False
    try:
        return parse_iso_date(str(filed)) <= as_of_date
    except ValueError:
        return False


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_companyfacts(cik: str) -> dict[str, Any]:
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "AXP eval validation contact@example.com"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def sec_get(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "AXP eval validation contact@example.com"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def cik_archive_number(cik: str) -> int:
    return int(cik.lstrip("0") or "0")


def filing_archive_base(cik: str, accession: str) -> str:
    accession_nodash = accession.replace("-", "")
    return (
        f"https://www.sec.gov/Archives/edgar/data/"
        f"{cik_archive_number(cik)}/{accession_nodash}"
    )


def accession_for_period(
    companyfacts: dict[str, Any],
    fiscal_year: int,
    fiscal_period: str,
    report_period: str,
    as_of_date: date,
) -> str:
    anchor_tags = [
        "NetIncomeLoss",
        "Revenues",
        "NetCashProvidedByUsedInOperatingActivities",
        "Assets",
    ]
    for tag in anchor_tags:
        matches = matching_fact_rows(
            companyfacts,
            [tag],
            fiscal_year,
            fiscal_period,
            report_period,
            QUARTERLY_FORMS | ANNUAL_FORMS,
            as_of_date,
        )
        if matches:
            accession = matches[0][2].get("accn")
            if accession:
                return str(accession)
    fail(
        f"cannot resolve filing accession for period ending {report_period} "
        f"({fiscal_period} {fiscal_year})"
    )


def primary_filing_instance(cik: str, accession: str) -> str:
    summary_url = f"{filing_archive_base(cik, accession)}/FilingSummary.xml"
    try:
        summary = sec_get(summary_url, timeout=30).decode("utf-8", "replace")
    except urllib.error.HTTPError:
        summary = ""
    if summary:
        reports = re.findall(
            r'<Report\b[^>]*instance="([^"]+)"[^>]*>.*?'
            r"<LongName>([^<]+)</LongName>.*?</Report>",
            summary,
            re.S,
        )
        for instance, long_name in reports:
            lowered = long_name.lower()
            if "statement of income" in lowered or "statement of operations" in lowered:
                return instance
        if reports:
            return reports[0][0]
    fail(f"cannot resolve primary filing instance for accession {accession}")


def load_filing_html(cik: str, accession: str) -> str:
    cache_key = (cik, accession)
    if cache_key in FILING_HTML_CACHE:
        return FILING_HTML_CACHE[cache_key]
    instance = primary_filing_instance(cik, accession)
    url = f"{filing_archive_base(cik, accession)}/{instance}"
    html = sec_get(url).decode("utf-8", "replace")
    FILING_HTML_CACHE[cache_key] = html
    return html


def parse_filing_contexts(html: str) -> dict[str, dict[str, Any]]:
    contexts: dict[str, dict[str, Any]] = {}
    for context_id, inner in re.findall(
        r'<xbrli:context\s+id="([^"]+)"[^>]*>(.*?)</xbrli:context>',
        html,
        re.S,
    ):
        start_match = re.search(r"<xbrli:startDate>([^<]+)</xbrli:startDate>", inner)
        end_match = re.search(r"<xbrli:endDate>([^<]+)</xbrli:endDate>", inner)
        instant_match = re.search(r"<xbrli:instant>([^<]+)</xbrli:instant>", inner)
        segments = re.findall(
            r'<xbrldi:explicitMember dimension="([^"]+)">([^<]+)</xbrldi:explicitMember>',
            inner,
        )
        contexts[context_id] = {
            "start": start_match.group(1) if start_match else None,
            "end": end_match.group(1) if end_match else None,
            "instant": instant_match.group(1) if instant_match else None,
            "segments": segments,
        }
    return contexts


def parse_ix_numeric_value(raw_text: str, attrs: str) -> int:
    cleaned = raw_text.strip().replace(",", "")
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = f"-{cleaned[1:-1]}"
    value = int(cleaned)
    scale_match = re.search(r'scale="(-?\d+)"', attrs)
    scale = int(scale_match.group(1)) if scale_match else 0
    return int(value * (10**scale))


def segment_member_matches(actual_member: str, expected_member: str) -> bool:
    if actual_member == expected_member:
        return True
    return actual_member.split(":")[-1] == expected_member.split(":")[-1]


def context_matches_segment(
    context: dict[str, Any],
    segment: dict[str, Any],
) -> bool:
    dimension = segment["dimension"]
    member = segment["member"]
    exclusive = segment.get("exclusive", True)
    segments = context.get("segments", [])
    if exclusive and len(segments) != 1:
        return False
    for actual_dimension, actual_member in segments:
        if actual_dimension != dimension:
            if exclusive:
                return False
            continue
        if segment_member_matches(actual_member, member):
            return True
    return False


def resolve_filing_dimensional(
    companyfacts: dict[str, Any],
    policy: dict[str, Any],
    cik: str,
    fiscal_year: int,
    fiscal_period: str,
    report_period: str,
    as_of_date: date,
) -> int | float:
    segment = policy.get("segment")
    if not segment or "dimension" not in segment or "member" not in segment:
        fail("filing_dimensional policy requires segment.dimension and segment.member")

    tag = policy.get("tag")
    if not tag:
        tags = candidate_tags(policy)
        if not tags:
            fail("filing_dimensional policy requires tag or tags")
        tag = tags[0]
    concept_name = tag if ":" in tag else f"us-gaap:{tag}"

    accession = accession_for_period(
        companyfacts,
        fiscal_year,
        fiscal_period,
        report_period,
        as_of_date,
    )
    html = load_filing_html(cik, accession)
    contexts = parse_filing_contexts(html)

    matches: list[tuple[int, str, int]] = []
    for attrs, raw_value in re.findall(
        r'<ix:nonFraction\s+([^>]+)>([^<]+)</ix:nonFraction>',
        html,
        re.S,
    ):
        concept = re.search(r'name="([^"]+)"', attrs)
        context_ref_match = re.search(r'contextRef="([^"]+)"', attrs)
        if not concept or not context_ref_match:
            continue
        if concept.group(1) != concept_name:
            continue
        context_ref = context_ref_match.group(1)
        context = contexts.get(context_ref)
        if not context:
            continue
        if context.get("end") != report_period:
            continue
        if not context_matches_segment(context, segment):
            continue
        duration = 0
        if context.get("start") and context.get("end"):
            duration = duration_days(
                {"start": context["start"], "end": context["end"]},
            )
        matches.append(
            (
                duration,
                context_ref,
                parse_ix_numeric_value(raw_value, attrs),
            )
        )

    if not matches:
        fail(
            f"missing filing dimensional fact {concept_name} "
            f"for {report_period} segment {segment}"
        )

    prefer_shortest = duration_preference(policy) == "shortest"
    multiplier = 1 if prefer_shortest else -1
    matches.sort(key=lambda item: (multiplier * item[0], item[1]))
    return matches[0][2]


def duration_days(row: dict[str, Any]) -> int:
    start = row.get("start")
    end = row.get("end")
    if not start or not end:
        return 0
    try:
        start_date = parse_iso_date(str(start))
        end_date = parse_iso_date(str(end))
    except ValueError:
        return 0
    return max((end_date - start_date).days, 0)


def metric_path(section: str, key: str) -> str:
    return f"{section}.{key}"


def split_metric_path(path: str) -> tuple[str, str]:
    section, key = path.split(".", 1)
    return section, key


def iter_policy_metrics(policies: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    metrics = []
    for section, section_metrics in policies["metrics"].items():
        for key, policy in section_metrics.items():
            metrics.append((section, key, policy))
    return metrics


def company_policy(
    policies: dict[str, Any],
    ticker_key: str,
    section: str,
    key: str,
) -> dict[str, Any]:
    base = policies["metrics"][section][key]
    overrides = policies.get("overrides", {}).get(ticker_key, {}).get(section, {}).get(key, {})
    return {**base, **overrides}


def candidate_tags(policy: dict[str, Any]) -> list[str]:
    return list(policy.get("tags", policy.get("candidate_tags", [])))


def duration_preference(policy: dict[str, Any]) -> str:
    return policy.get("duration", policy.get("duration_preference", "longest"))


def matching_fact_rows(
    companyfacts: dict[str, Any],
    tags: list[str],
    fiscal_year: int,
    fiscal_period: str,
    report_period: str,
    forms: set[str],
    as_of_date: date,
) -> list[tuple[str, str, dict[str, Any]]]:
    facts = companyfacts.get("facts", {}).get("us-gaap", {})
    matches = []
    for tag in tags:
        units = facts.get(tag, {}).get("units", {}).get("USD", [])
        for row in units:
            if not filed_on_or_before(row, as_of_date):
                continue
            if row.get("fy") != fiscal_year:
                continue
            if row.get("fp") != fiscal_period:
                continue
            if row.get("end") != report_period:
                continue
            if row.get("form") not in forms:
                continue
            matches.append((str(row.get("filed", "")), tag, row))
    return matches


def prior_ytd_fact_rows(
    companyfacts: dict[str, Any],
    tag: str,
    fiscal_year: int,
    prior_fiscal_period: str,
    report_period: str,
    as_of_date: date,
) -> list[tuple[str, str, dict[str, Any]]]:
    facts = companyfacts.get("facts", {}).get("us-gaap", {})
    matches = []
    for row in facts.get(tag, {}).get("units", {}).get("USD", []):
        if not filed_on_or_before(row, as_of_date):
            continue
        if row.get("fy") != fiscal_year:
            continue
        if row.get("fp") != prior_fiscal_period:
            continue
        if row.get("form") not in QUARTERLY_FORMS:
            continue
        if not row.get("end") or row.get("end") >= report_period:
            continue
        matches.append((str(row.get("filed", "")), tag, row))
    return matches


def choose_fact_row(
    companyfacts: dict[str, Any],
    tags: list[str],
    fiscal_year: int,
    fiscal_period: str,
    report_period: str,
    duration_preference: str,
    forms: set[str],
    as_of_date: date,
) -> tuple[str, dict[str, Any]]:
    matches = matching_fact_rows(
        companyfacts,
        tags,
        fiscal_year,
        fiscal_period,
        report_period,
        forms,
        as_of_date,
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
    as_of_date: date,
) -> dict[str, Any]:
    matches = prior_ytd_fact_rows(
        companyfacts,
        tag,
        fiscal_year,
        prior_fiscal_period,
        report_period,
        as_of_date,
    )
    if not matches:
        fail(
            f"missing {prior_fiscal_period} YTD SEC fact for {tag} before {report_period}"
        )

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
    policy: dict[str, Any],
    fiscal_year: int,
    fiscal_period: str,
    report_period: str,
    selection_mode: str,
    as_of_date: date,
) -> tuple[str, dict[str, Any], dict[str, Any], str]:
    tags = candidate_tags(policy)
    source_fp = "FY" if selection_mode == "q4_from_fy" else fiscal_period
    source_forms = ANNUAL_FORMS if selection_mode == "q4_from_fy" else QUARTERLY_FORMS
    source_tag, source_row = choose_fact_row(
        companyfacts,
        tags,
        fiscal_year,
        source_fp,
        report_period,
        "longest",
        source_forms,
        as_of_date,
    )
    prior_row = choose_prior_ytd_fact_row(
        companyfacts,
        source_tag,
        fiscal_year,
        PRIOR_FISCAL_PERIOD[fiscal_period],
        report_period,
        as_of_date,
    )
    source_name = "FY 10-K" if selection_mode == "q4_from_fy" else f"{fiscal_period} 10-Q"
    derivation = (
        f"{source_name} year-to-date value minus prior 10-Q year-to-date value"
    )
    return source_tag, source_row, prior_row, derivation


def resolve_direct(
    companyfacts: dict[str, Any],
    policy: dict[str, Any],
    fiscal_year: int,
    fiscal_period: str,
    report_period: str,
    selection_mode: str,
    as_of_date: date,
) -> int | float:
    tags = candidate_tags(policy)
    if selection_mode == "q4_from_fy":
        _, row = choose_fact_row(
            companyfacts,
            tags,
            fiscal_year,
            "FY",
            report_period,
            duration_preference(policy),
            ANNUAL_FORMS,
            as_of_date,
        )
        return row.get("val")

    _, row = choose_fact_row(
        companyfacts,
        tags,
        fiscal_year,
        fiscal_period,
        report_period,
        duration_preference(policy),
        QUARTERLY_FORMS,
        as_of_date,
    )
    return row.get("val")


def resolve_current_period_from_cumulative(
    companyfacts: dict[str, Any],
    policy: dict[str, Any],
    fiscal_year: int,
    fiscal_period: str,
    report_period: str,
    selection_mode: str,
    as_of_date: date,
) -> int | float:
    prefer_ytd_derivation = (
        duration_preference(policy) == "longest"
        and fiscal_period in PRIOR_FISCAL_PERIOD
    )
    if selection_mode != "q4_from_fy" and not prefer_ytd_derivation:
        tags = candidate_tags(policy)
        direct_matches = matching_fact_rows(
            companyfacts,
            tags,
            fiscal_year,
            fiscal_period,
            report_period,
            QUARTERLY_FORMS,
            as_of_date,
        )
        if direct_matches:
            return resolve_direct(
                companyfacts,
                policy,
                fiscal_year,
                fiscal_period,
                report_period,
                selection_mode,
                as_of_date,
            )

    if fiscal_period not in PRIOR_FISCAL_PERIOD and selection_mode != "q4_from_fy":
        fail(
            f"cannot derive current period for {tags} @ {report_period}; "
            "no direct quarterly fact and no prior YTD period"
        )

    _, source_row, prior_row, _ = derived_quarter_value(
        companyfacts,
        policy,
        fiscal_year,
        fiscal_period,
        report_period,
        selection_mode,
        as_of_date,
    )
    return source_row.get("val") - prior_row.get("val")


def resolve_formula(
    resolved_values: dict[str, int | float | None],
    policy: dict[str, Any],
) -> int | float:
    formula = policy["formula"]
    op = formula["op"]
    if op == "difference":
        minuend = resolved_values[formula["minuend"]]
        subtrahend = resolved_values[formula["subtrahend"]]
        if minuend is None or subtrahend is None:
            fail(f"cannot compute formula; missing inputs for {formula}")
        return minuend - subtrahend
    if op == "sum":
        terms = formula["terms"]
        missing = [term for term in terms if resolved_values.get(term) is None]
        if missing:
            fail(f"cannot compute formula; missing inputs: {missing}")
        return sum(resolved_values[term] for term in terms)
    fail(f"unsupported formula op: {op}")


def resolve_metric(
    companyfacts: dict[str, Any],
    policies: dict[str, Any],
    ticker_key: str,
    section: str,
    key: str,
    period: dict[str, Any],
    resolved_values: dict[str, int | float | None],
    as_of_date: date,
    cik: str | None = None,
) -> int | float:
    policy = company_policy(policies, ticker_key, section, key)
    method = policy["method"]
    if method == "current_period_from_cumulative_if_needed":
        method = CUMULATIVE_METHOD
    fiscal_year = period["fiscal_year"]
    fiscal_period = period["fiscal_period"]
    report_period = period["report_period"]
    selection_mode = period["selection_mode"]

    if method == "formula":
        return resolve_formula(resolved_values, policy)

    if method == "direct":
        return resolve_direct(
            companyfacts,
            policy,
            fiscal_year,
            fiscal_period,
            report_period,
            selection_mode,
            as_of_date,
        )

    if method == CUMULATIVE_METHOD:
        return resolve_current_period_from_cumulative(
            companyfacts,
            policy,
            fiscal_year,
            fiscal_period,
            report_period,
            selection_mode,
            as_of_date,
        )

    if method == "filing_dimensional":
        if not cik:
            fail(f"filing_dimensional for {metric_path(section, key)} requires cik")
        return resolve_filing_dimensional(
            companyfacts,
            policy,
            cik,
            fiscal_year,
            fiscal_period,
            report_period,
            as_of_date,
        )

    fail(f"unsupported method {method} for {metric_path(section, key)}")


def revenue_tags(policies: dict[str, Any]) -> list[str]:
    return candidate_tags(policies["metrics"]["income_statement"]["revenue"])


def latest_report_period(
    companyfacts: dict[str, Any],
    policies: dict[str, Any],
    as_of_date: date,
) -> dict[str, Any]:
    facts = companyfacts.get("facts", {}).get("us-gaap", {})
    candidates: list[tuple[str, str, int, str, str]] = []
    for tag in revenue_tags(policies):
        for row in facts.get(tag, {}).get("units", {}).get("USD", []):
            if not filed_on_or_before(row, as_of_date):
                continue
            fiscal_year = row.get("fy")
            fiscal_period = row.get("fp")
            report_period = row.get("end")
            filed = str(row.get("filed", ""))
            if not isinstance(fiscal_year, int):
                continue
            if not report_period:
                continue
            if row.get("form") in QUARTERLY_FORMS and fiscal_period in QUARTERLY_FPS:
                candidates.append(
                    (report_period, filed, fiscal_year, fiscal_period, "direct")
                )
            if row.get("form") in ANNUAL_FORMS and fiscal_period == "FY":
                candidates.append(
                    (report_period, filed, fiscal_year, "Q4", "q4_from_fy")
                )

    candidates = sorted(set(candidates), reverse=True)
    for report_period, _, fiscal_year, fiscal_period, selection_mode in candidates:
        period = {
            "fiscal_year": fiscal_year,
            "fiscal_period": fiscal_period,
            "report_period": report_period,
            "selection_mode": selection_mode,
        }
        try:
            for section, key, policy in iter_policy_metrics(policies):
                if policy["method"] == "formula":
                    continue
                if company_policy(policies, "", section, key).get("method") == "filing_dimensional":
                    continue
                resolve_metric(
                    companyfacts,
                    policies,
                    "",
                    section,
                    key,
                    period,
                    {},
                    as_of_date,
                )
        except SystemExit:
            continue
        return period

    fail("could not find a latest SEC reporting period with all required facts")


def period_type_for(fiscal_period: str) -> str:
    return "year_end" if fiscal_period == "Q4" else "interim"


def build_ticker_entry(
    ticker_key: str,
    policies: dict[str, Any],
    as_of_date: date,
) -> dict[str, Any]:
    meta = metadata()[ticker_key]
    companyfacts = load_companyfacts(meta["cik"])
    latest = latest_report_period(companyfacts, policies, as_of_date)
    period = {
        "fiscal_year": latest["fiscal_year"],
        "fiscal_period": latest["fiscal_period"],
        "report_period": latest["report_period"],
        "period_type": period_type_for(latest["fiscal_period"]),
        "selection_mode": latest["selection_mode"],
    }

    resolved_values: dict[str, int | float | None] = {}
    core_values: dict[str, dict[str, int | float | None]] = {}

    for section, key, policy in iter_policy_metrics(policies):
        if policy["method"] == "formula":
            continue
        path = metric_path(section, key)
        value = resolve_metric(
            companyfacts,
            policies,
            ticker_key,
            section,
            key,
            latest,
            resolved_values,
            as_of_date,
            cik=meta["cik"],
        )
        resolved_values[path] = value
        core_values.setdefault(section, {})[key] = value

    for section, key, policy in iter_policy_metrics(policies):
        if policy["method"] != "formula":
            continue
        path = metric_path(section, key)
        value = resolve_metric(
            companyfacts,
            policies,
            ticker_key,
            section,
            key,
            latest,
            resolved_values,
            as_of_date,
            cik=meta["cik"],
        )
        resolved_values[path] = value
        core_values.setdefault(section, {})[key] = value

    return {
        "period": {
            "fiscal_year": period["fiscal_year"],
            "fiscal_period": period["fiscal_period"],
            "report_period": period["report_period"],
            "period_type": period["period_type"],
        },
        "core_values": core_values,
    }


def grading_config(policies: dict[str, Any]) -> dict[str, Any]:
    return policies.get("grading", policies.get("score_policy", {}))


def build_fixture(
    ticker_keys: list[str],
    policies: dict[str, Any],
    as_of_date: date,
) -> dict[str, Any]:
    return {
        "as_of_date": as_of_date.isoformat(),
        "grading": grading_config(policies),
        "expected_by_ticker": {
            ticker_key: build_ticker_entry(ticker_key, policies, as_of_date)
            for ticker_key in ticker_keys
        },
    }


def inspect_ticker(
    ticker_key: str,
    policies: dict[str, Any],
    as_of_date: date,
) -> dict[str, Any]:
    meta = metadata()[ticker_key]
    entry = build_ticker_entry(ticker_key, policies, as_of_date)
    return {
        "ticker": meta["ticker"],
        "cik": meta["cik"],
        **entry["period"],
        "companyfacts_url": f"https://data.sec.gov/api/xbrl/companyfacts/CIK{meta['cik']}.json",
        "core_values": entry["core_values"],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inspect or generate SEC companyfacts core financial fixtures."
    )
    parser.add_argument(
        "tickers",
        nargs="*",
        help="Ticker keys to inspect. Defaults to all scenario tickers.",
    )
    parser.add_argument(
        "--as-of-date",
        default=DEFAULT_AS_OF_DATE,
        help=f"Only use SEC facts filed on or before this date (default: {DEFAULT_AS_OF_DATE}).",
    )
    parser.add_argument(
        "--policies",
        default=str(DEFAULT_POLICIES_PATH),
        help="Path to metric-policies.json",
    )
    parser.add_argument(
        "--fixture-json",
        help="Write eval fixture JSON to this path.",
    )
    parser.add_argument(
        "--check-drift",
        help="Regenerate fixture to a temp file and compare against this committed fixture path.",
    )
    return parser.parse_args()


def compare_fixtures(expected: dict[str, Any], actual: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    expected_tickers = set(expected.get("expected_by_ticker", {}))
    actual_tickers = set(actual.get("expected_by_ticker", {}))
    if expected_tickers != actual_tickers:
        errors.append(
            f"ticker set mismatch: expected={sorted(expected_tickers)} actual={sorted(actual_tickers)}"
        )
    for ticker_key in sorted(expected_tickers & actual_tickers):
        expected_entry = expected["expected_by_ticker"][ticker_key]
        actual_entry = actual["expected_by_ticker"][ticker_key]
        for period_field in ("fiscal_year", "fiscal_period", "report_period", "period_type"):
            exp_period = expected_entry["period"]
            act_period = actual_entry["period"]
            if exp_period.get(period_field) != act_period.get(period_field):
                errors.append(
                    f"{ticker_key}.period.{period_field} mismatch: "
                    f"{act_period.get(period_field)} != {exp_period.get(period_field)}"
                )
        for section, section_values in expected_entry.get("core_values", {}).items():
            for key, expected_value in section_values.items():
                actual_value = actual_entry.get("core_values", {}).get(section, {}).get(key)
                if actual_value != expected_value:
                    errors.append(
                        f"{ticker_key}.core_values.{section}.{key} mismatch: "
                        f"{actual_value} != {expected_value}"
                    )
    return errors


def main() -> None:
    args = parse_args()
    known = metadata()
    ticker_keys = [ticker.lower() for ticker in args.tickers] or registry_ticker_keys()
    unknown = [ticker for ticker in ticker_keys if ticker not in known]
    if unknown:
        fail(f"unknown ticker(s): {', '.join(unknown)}")

    as_of_date = parse_iso_date(args.as_of_date)
    policies = load_json(Path(args.policies))

    if args.check_drift:
        committed = load_json(Path(args.check_drift))
        generated = build_fixture(ticker_keys, policies, as_of_date)
        errors = compare_fixtures(committed, generated)
        if errors:
            fail("fixture drift detected:\n" + "\n".join(errors))
        print(f"fixture drift check passed for {args.check_drift}")
        return

    if args.fixture_json:
        fixture = build_fixture(ticker_keys, policies, as_of_date)
        output_path = Path(args.fixture_json)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(fixture, indent=2) + "\n", encoding="utf-8")
        print(f"wrote fixture to {output_path}")
        return

    results = [inspect_ticker(ticker, policies, as_of_date) for ticker in ticker_keys]
    print(json.dumps(results, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
