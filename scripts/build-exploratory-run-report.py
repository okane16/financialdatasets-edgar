#!/usr/bin/env python3
"""Build a SQL-backed exploratory HTML report for the latest 20-variant AXP run."""

from __future__ import annotations

import argparse
import ast
import csv
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SCENARIO_ID = "financialdatasets-vs-edgar-latest-reported-financials"
REPORT_RUN_ID = "latest-quarter-run-20"
DATA_DIR = REPO_ROOT / "docs" / "reports" / "data" / REPORT_RUN_ID
AXP_DOWNLOAD_DIR = REPO_ROOT / ".axp" / "downloads" / REPORT_RUN_ID
DEFAULT_OUTPUT = REPO_ROOT / "docs" / "reports" / "latest-quarter-run-report.html"

FINANCIAL_FIELDS = [
    "revenue",
    "net_income",
    "total_assets",
    "operating_cash_flow",
    "capital_expenditures",
    "free_cash_flow",
]


def provider_for_variant(variant_id: str) -> str:
    if variant_id.endswith("-financialdatasets-rest"):
        return "Financial Datasets"
    if variant_id.endswith("-sec-edgar"):
        return "SEC EDGAR"
    raise ValueError(f"Unsupported variant id: {variant_id}")


def ticker_for_variant(variant_id: str) -> str:
    return variant_id.split("-", 1)[0].upper()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def discover_latest_variants(scenario_id: str = SCENARIO_ID) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for run_json in sorted((REPO_ROOT / ".axp" / "runs").glob("*/variants/*/run.json")):
        run = load_json(run_json)
        if run.get("scenario_id") != scenario_id:
            continue
        variant_id = run.get("variant_id")
        started_at = run.get("started_at") or ""
        if not variant_id:
            continue
        current = latest.get(variant_id)
        if current is None or started_at > current["run"]["started_at"]:
            latest[variant_id] = {"run": run, "variant_dir": run_json.parent}

    variants = [latest[key] for key in sorted(latest)]
    if len(variants) != 20:
        raise RuntimeError(f"Expected 20 latest variants for {scenario_id}, found {len(variants)}")
    return variants


def export_axp_parquet(variants: list[dict[str, Any]], run_id: str = REPORT_RUN_ID) -> None:
    if AXP_DOWNLOAD_DIR.exists():
        shutil.rmtree(AXP_DOWNLOAD_DIR)
    AXP_DOWNLOAD_DIR.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="axp-combined-run-") as tmp:
        synthetic = Path(tmp) / run_id
        variants_dir = synthetic / "variants"
        variants_dir.mkdir(parents=True)
        for item in variants:
            target = item["variant_dir"].resolve()
            os.symlink(target, variants_dir / item["run"]["variant_id"])
        subprocess.run(
            ["axp", "parquet", "axp", str(synthetic), str(AXP_DOWNLOAD_DIR)],
            cwd=REPO_ROOT,
            check=True,
            text=True,
        )


def validate_axp_parquet() -> None:
    subprocess.run(["axp", "parquet", "validate", str(AXP_DOWNLOAD_DIR)], cwd=REPO_ROOT, check=True)


def copy_parquet_into_report_bundle() -> Path:
    target = DATA_DIR / "parquet"
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    for source in sorted(AXP_DOWNLOAD_DIR.glob("*.parquet")):
        shutil.copy2(source, target / source.name)
    return target


def extract_expected_fixture() -> dict[str, dict[str, Any]]:
    path = REPO_ROOT / "scripts" / "financial-values-match-test.sh"
    text = path.read_text(encoding="utf-8")
    start = text.index("expected_by_ticker =")
    end = text.index("\ndef fail", start)
    module = ast.parse(text[start:end])
    assignment = module.body[0]
    if not isinstance(assignment, ast.Assign):
        raise RuntimeError("Could not parse expected_by_ticker assignment")
    return ast.literal_eval(assignment.value)


def answer_path_for_variant(variant_dir: Path) -> Path | None:
    fs_diff = variant_dir / "fs-diff" / "000000.json"
    if fs_diff.is_file():
        diff = load_json(fs_diff)
        root = diff.get("after_root") or diff.get("before_root")
        if root:
            candidate = Path(root) / "answer.json"
            if candidate.is_file():
                return candidate
    sandbox_match = re.search(r"\.axp/sandboxes/([^/]+)/workspace", str(fs_diff))
    if sandbox_match:
        candidate = REPO_ROOT / ".axp" / "sandboxes" / sandbox_match.group(1) / "workspace" / "answer.json"
        if candidate.is_file():
            return candidate
    return None


def parse_failure(stderr_tail: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for line in stderr_tail.splitlines():
        line = line.strip()
        if not line:
            continue
        within = re.match(r"^([A-Za-z0-9_]+) mismatch: (.*?) not within .* of (.*?)$", line)
        exact = re.match(r"^([A-Za-z0-9_]+) mismatch: (.*?) != (.*?)$", line)
        match = within or exact
        if match:
            rows.append({"field": match.group(1), "actual": match.group(2), "expected": match.group(3), "message": line})
        else:
            rows.append({"field": "", "actual": "", "expected": "", "message": line})
    return rows


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def write_sidecar_csvs(variants: list[dict[str, Any]], expected: dict[str, dict[str, Any]]) -> dict[str, Path]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    expected_rows = []
    for ticker_key, item in sorted(expected.items()):
        financials = item["financials_usd"]
        expected_rows.append(
            {
                "ticker": item["ticker"],
                "fiscal_year": item["fiscal_year"],
                "fiscal_period": item["fiscal_period"],
                "report_period": item["report_period"],
                **{field: financials[field] for field in FINANCIAL_FIELDS},
            }
        )

    actual_rows = []
    comparison_rows = []
    failure_rows = []
    for item in variants:
        run = item["run"]
        variant_id = run["variant_id"]
        ticker = ticker_for_variant(variant_id)
        provider = provider_for_variant(variant_id)
        answer_path = answer_path_for_variant(item["variant_dir"])
        answer = load_json(answer_path) if answer_path else {}
        financials = answer.get("financials_usd", {}) if isinstance(answer, dict) else {}
        ratios = answer.get("ratios", {}) if isinstance(answer, dict) else {}

        actual_rows.append(
            {
                "variant_id": variant_id,
                "run_group_id": run["run_group_id"],
                "ticker": ticker,
                "provider": provider,
                "status": run["status"],
                "answer_path": str(answer_path.relative_to(REPO_ROOT)) if answer_path else "",
                "fiscal_year": answer.get("fiscal_year", ""),
                "report_period": answer.get("report_period", ""),
                **{field: financials.get(field, "") for field in FINANCIAL_FIELDS},
                "net_margin": ratios.get("net_margin", ""),
                "free_cash_flow_margin": ratios.get("free_cash_flow_margin", ""),
            }
        )

        fixture = expected[ticker.lower()]
        for field in FINANCIAL_FIELDS:
            actual = financials.get(field)
            target = fixture["financials_usd"][field]
            delta = actual - target if isinstance(actual, (int, float)) else ""
            comparison_rows.append(
                {
                    "variant_id": variant_id,
                    "ticker": ticker,
                    "provider": provider,
                    "metric": field,
                    "actual": actual if actual is not None else "",
                    "expected": target,
                    "delta": delta,
                    "delta_pct": (delta / target) if isinstance(delta, (int, float)) and target else "",
                }
            )

        for test in run.get("tests", []):
            if test.get("exit_code") == 0:
                continue
            parsed = parse_failure(test.get("stderr_tail") or "")
            if not parsed:
                parsed = [{"field": "", "actual": "", "expected": "", "message": ""}]
            for failure in parsed:
                failure_rows.append(
                    {
                        "variant_id": variant_id,
                        "run_group_id": run["run_group_id"],
                        "ticker": ticker,
                        "provider": provider,
                        "test_name": test.get("name", ""),
                        **failure,
                    }
                )

    paths = {
        "expected": DATA_DIR / "expected_values.csv",
        "actual": DATA_DIR / "actual_values.csv",
        "comparisons": DATA_DIR / "metric_comparisons.csv",
        "failures": DATA_DIR / "failures.csv",
    }
    write_csv(paths["expected"], expected_rows, ["ticker", "fiscal_year", "fiscal_period", "report_period", *FINANCIAL_FIELDS])
    write_csv(
        paths["actual"],
        actual_rows,
        [
            "variant_id",
            "run_group_id",
            "ticker",
            "provider",
            "status",
            "answer_path",
            "fiscal_year",
            "report_period",
            *FINANCIAL_FIELDS,
            "net_margin",
            "free_cash_flow_margin",
        ],
    )
    write_csv(paths["comparisons"], comparison_rows, ["variant_id", "ticker", "provider", "metric", "actual", "expected", "delta", "delta_pct"])
    write_csv(paths["failures"], failure_rows, ["variant_id", "run_group_id", "ticker", "provider", "test_name", "field", "actual", "expected", "message"])
    return paths


def duckdb_query(sql: str, run_id: str = REPORT_RUN_ID) -> list[dict[str, Any]]:
    result = subprocess.run(
        ["axp", "duckdb", "query", run_id, sql],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    rows = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def money_short(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    sign = "-" if number < 0 else ""
    number = abs(number)
    if number >= 1_000_000_000:
        return f"{sign}${number / 1_000_000_000:.1f}B"
    if number >= 1_000_000:
        return f"{sign}${number / 1_000_000:.0f}M"
    return f"{sign}${number:,.0f}"


def pct(value: Any) -> str:
    try:
        return f"{float(value):.1f}%"
    except (TypeError, ValueError):
        return ""


def rel(path: Path) -> str:
    return str(path.relative_to(REPO_ROOT))


def query_catalog(paths: dict[str, Path] | None = None) -> dict[str, str]:
    return {
        "summary": """
SELECT
  COUNT(*) AS total_variants,
  SUM(CASE WHEN status = 'pass' THEN 1 ELSE 0 END) AS passed,
  SUM(CASE WHEN status <> 'pass' THEN 1 ELSE 0 END) AS failed,
  ROUND(100.0 * SUM(CASE WHEN status = 'pass' THEN 1 ELSE 0 END) / COUNT(*), 1) AS pass_rate
FROM runs;
""".strip(),
        "provider": """
SELECT
  CASE
    WHEN variant_id LIKE '%financialdatasets-rest' THEN 'Financial Datasets'
    WHEN variant_id LIKE '%sec-edgar' THEN 'SEC EDGAR'
    ELSE 'Other'
  END AS provider,
  COUNT(*) AS variants,
  SUM(CASE WHEN status = 'pass' THEN 1 ELSE 0 END) AS passed,
  SUM(CASE WHEN status <> 'pass' THEN 1 ELSE 0 END) AS failed,
  ROUND(100.0 * SUM(CASE WHEN status = 'pass' THEN 1 ELSE 0 END) / COUNT(*), 1) AS pass_rate,
  ROUND(SUM(cost_usd_micros) / 1000000.0, 4) AS cost_usd
FROM runs
GROUP BY provider
ORDER BY provider;
""".strip(),
        "matrix": """
SELECT
  upper(split_part(variant_id, '-', 1)) AS ticker,
  CASE
    WHEN variant_id LIKE '%financialdatasets-rest' THEN 'Financial Datasets'
    ELSE 'SEC EDGAR'
  END AS provider,
  status,
  run_group_id
FROM runs
ORDER BY ticker, provider;
""".strip(),
        "failure_fields": f"""
SELECT
  COALESCE(NULLIF(field, ''), test_name) AS failure_bucket,
  COUNT(*) AS failures
FROM failures
GROUP BY failure_bucket
ORDER BY failures DESC, failure_bucket;
""".strip(),
        "failures": """
SELECT
  ticker,
  provider,
  variant_id,
  test_name,
  field,
  actual,
  expected,
  message
FROM failures
ORDER BY ticker, provider, test_name;
""".strip(),
        "failure_thoughts": """
SELECT *
FROM failure_thoughts
ORDER BY variant_id, test_name, field, seq;
""".strip(),
        "runtime": """
SELECT
  CASE
    WHEN variant_id LIKE '%financialdatasets-rest' THEN 'Financial Datasets'
    ELSE 'SEC EDGAR'
  END AS provider,
  ROUND(AVG(date_diff('second', CAST(started_at AS TIMESTAMP), CAST(ended_at AS TIMESTAMP))), 1) AS avg_duration_sec,
  ROUND(SUM(cost_usd_micros) / 1000000.0, 4) AS total_agent_cost_usd,
  ROUND(AVG(num_turns), 1) AS avg_turns
FROM runs
GROUP BY provider
ORDER BY provider;
""".strip(),
        "periods": """
SELECT
  a.ticker,
  a.provider,
  a.report_period AS actual_period,
  e.report_period AS expected_period,
  CASE WHEN a.report_period = e.report_period THEN 'match' ELSE 'mismatch' END AS period_status
FROM actual_values a
JOIN expected_values e USING (ticker)
ORDER BY a.ticker, a.provider;
""".strip(),
        "expected_values": """
SELECT *
FROM expected_values
ORDER BY ticker;
""".strip(),
        "schema_overview": """
WITH object_catalog(table_name, object_kind, origin, origin_class, source_path, provenance) AS (
  VALUES
    ('actual_values', 'table', 'Custom', 'custom', 'docs/reports/data/latest-quarter-run-20/actual_values.csv', 'Custom CSV generated from answer.json outputs by scripts/build-exploratory-run-report.py'),
    ('agent_events', 'table', 'Default AXP', 'default', 'docs/reports/data/latest-quarter-run-20/parquet/agent_events.parquet', 'Default AXP parquet export'),
    ('expected_values', 'table', 'Custom', 'custom', 'docs/reports/data/latest-quarter-run-20/expected_values.csv', 'Custom CSV generated from scripts/financial-values-match-test.sh fixtures'),
    ('failure_thoughts', 'view', 'Custom', 'custom', 'Derived from failures, runs, and agent_events', 'Custom DuckDB view joining failed tests to the final recorded agent_thought_chunk events'),
    ('failures', 'table', 'Custom', 'custom', 'docs/reports/data/latest-quarter-run-20/failures.csv', 'Custom CSV parsed from failed AXP test stderr tails'),
    ('harness_spans', 'table', 'Default AXP', 'default', 'docs/reports/data/latest-quarter-run-20/parquet/harness_spans.parquet', 'Default AXP parquet export'),
    ('messages', 'table', 'Default AXP', 'default', 'docs/reports/data/latest-quarter-run-20/parquet/messages.parquet', 'Default AXP parquet export'),
    ('metric_comparisons', 'table', 'Custom', 'custom', 'docs/reports/data/latest-quarter-run-20/metric_comparisons.csv', 'Custom CSV comparing answer.json values to expected fixtures'),
    ('runs', 'table', 'Default AXP', 'default', 'docs/reports/data/latest-quarter-run-20/parquet/runs.parquet', 'Default AXP parquet export'),
    ('tests', 'table', 'Default AXP', 'default', 'docs/reports/data/latest-quarter-run-20/parquet/tests.parquet', 'Default AXP parquet export'),
    ('tool_calls', 'table', 'Default AXP', 'default', 'docs/reports/data/latest-quarter-run-20/parquet/tool_calls.parquet', 'Default AXP parquet export')
),
columns AS (
  SELECT 'actual_values' AS table_name, cid, name, type FROM pragma_table_info('actual_values')
  UNION ALL
  SELECT 'agent_events' AS table_name, cid, name, type FROM pragma_table_info('agent_events')
  UNION ALL
  SELECT 'expected_values' AS table_name, cid, name, type FROM pragma_table_info('expected_values')
  UNION ALL
  SELECT 'failure_thoughts' AS table_name, cid, name, type FROM pragma_table_info('failure_thoughts')
  UNION ALL
  SELECT 'failures' AS table_name, cid, name, type FROM pragma_table_info('failures')
  UNION ALL
  SELECT 'harness_spans' AS table_name, cid, name, type FROM pragma_table_info('harness_spans')
  UNION ALL
  SELECT 'messages' AS table_name, cid, name, type FROM pragma_table_info('messages')
  UNION ALL
  SELECT 'metric_comparisons' AS table_name, cid, name, type FROM pragma_table_info('metric_comparisons')
  UNION ALL
  SELECT 'runs' AS table_name, cid, name, type FROM pragma_table_info('runs')
  UNION ALL
  SELECT 'tests' AS table_name, cid, name, type FROM pragma_table_info('tests')
  UNION ALL
  SELECT 'tool_calls' AS table_name, cid, name, type FROM pragma_table_info('tool_calls')
)
SELECT
  c.table_name,
  o.object_kind,
  o.origin,
  o.origin_class,
  o.source_path,
  o.provenance,
  c.name AS column_name,
  c.type AS column_type
FROM columns c
JOIN object_catalog o USING (table_name)
ORDER BY c.table_name, c.cid;
""".strip(),
    }


def script_json(value: Any) -> str:
    return json.dumps(value, indent=2).replace("</", "<\\/")


def panel_attrs(query_id: str) -> str:
    return f'class="panel query-panel" tabindex="0" data-query-id="{html.escape(query_id)}"'


def render_bar(label: str, value: float, max_value: float, accent: str = "ok") -> str:
    width = 0 if max_value == 0 else max(2, min(100, value / max_value * 100))
    return (
        f'<div class="bar-row"><span>{html.escape(label)}</span>'
        f'<div class="bar-track"><div class="bar-fill {accent}" style="width:{width:.1f}%"></div></div>'
        f'<strong>{value:g}</strong></div>'
    )


def render_live_report_html(queries: dict[str, str], generated_at: str) -> str:
    data_base = f"data/{REPORT_RUN_ID}"
    html_text = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Latest Quarterly Financials Eval Canvas</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f6f7f9;
      --ink: #18212f;
      --muted: #657083;
      --panel: #ffffff;
      --line: #d9dee7;
      --ok: #176b5b;
      --ok-bg: #dff3ee;
      --fail: #b13b32;
      --fail-bg: #f7dfdd;
      --accent: #1f5f8b;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font: 14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    header {
      padding: 28px 32px 18px;
      border-bottom: 1px solid var(--line);
      background: #fff;
    }
    h1 { margin: 0 0 8px; font-size: 28px; letter-spacing: 0; }
    h2 { margin: 0 0 14px; font-size: 16px; }
    p { margin: 0; color: var(--muted); max-width: 940px; }
    main { padding: 24px 32px 40px; }
    .grid { display: grid; gap: 16px; }
    .kpis { grid-template-columns: repeat(4, minmax(140px, 1fr)); margin-bottom: 16px; }
    .two { grid-template-columns: minmax(0, 1.1fr) minmax(0, .9fr); }
    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 18px;
      box-shadow: 0 1px 2px rgba(20, 31, 46, .05);
      cursor: pointer;
    }
    .panel:focus { outline: 2px solid var(--accent); outline-offset: 2px; }
    .status { margin-top: 16px; color: var(--muted); font-size: 12px; }
    .kpi-grid { display: grid; grid-template-columns: repeat(4, minmax(120px, 1fr)); gap: 14px; }
    .kpi span, .subtle { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .04em; }
    .kpi strong { display: block; margin-top: 6px; font-size: 30px; }
    .provider-row { display: grid; grid-template-columns: 190px 1fr 56px; align-items: center; gap: 12px; margin: 12px 0; }
    .provider-row span { display: block; color: var(--muted); font-size: 12px; margin-top: 2px; }
    .rate, .bar-track { height: 10px; background: #edf0f4; border-radius: 999px; overflow: hidden; }
    .rate div, .bar-fill { height: 100%; background: var(--ok); }
    .bar-fill.fail { background: var(--fail); }
    .matrix { display: grid; grid-template-columns: repeat(5, minmax(120px, 1fr)); gap: 10px; }
    .matrix-cell { border: 1px solid var(--line); border-radius: 6px; padding: 10px; min-height: 74px; }
    .matrix-cell.pass { background: var(--ok-bg); border-color: #abd7cc; }
    .matrix-cell.fail { background: var(--fail-bg); border-color: #e3aca7; }
    .matrix-cell span, .matrix-cell b { display: block; }
    .matrix-cell small { color: var(--muted); display: block; min-height: 34px; }
    .bar-row { display: grid; grid-template-columns: 150px 1fr 36px; gap: 10px; align-items: center; margin: 10px 0; }
    .tabbar { display: flex; gap: 8px; margin-top: 18px; }
    .tab-button {
      border: 1px solid var(--line);
      background: #fff;
      color: var(--ink);
      border-radius: 6px;
      padding: 8px 12px;
    }
    .tab-button.active { background: var(--ink); border-color: var(--ink); color: #fff; }
    .tab-view { display: none; }
    .tab-view.active { display: block; }
    .catalog-page { cursor: default; }
    .catalog-shell {
      display: grid;
      grid-template-columns: 280px minmax(0, 1fr);
      min-height: 620px;
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
      background: #fff;
    }
    .catalog-sidebar { border-right: 1px solid var(--line); background: #f9fafc; }
    .catalog-sidebar-head { padding: 14px; border-bottom: 1px solid var(--line); }
    .catalog-sidebar-head span { display: block; color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .04em; }
    .catalog-table-list { padding: 8px; display: grid; gap: 4px; }
    .catalog-table-button {
      width: 100%;
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 8px;
      align-items: center;
      border: 0;
      background: transparent;
      color: var(--ink);
      text-align: left;
      border-radius: 6px;
      padding: 9px 10px;
    }
    .catalog-table-button:hover, .catalog-table-button.active { background: #e8edf4; }
    .catalog-table-button small { color: var(--muted); }
    .catalog-detail { padding: 18px; overflow: auto; }
    .catalog-detail-section { display: none; }
    .catalog-detail-section.active { display: block; }
    .catalog-detail-head { display: flex; justify-content: space-between; gap: 16px; align-items: flex-start; margin-bottom: 12px; }
    .catalog-detail-head h3 { margin: 0; font-size: 22px; }
    .catalog-meta { color: var(--muted); font-size: 12px; }
    .catalog-type { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; color: var(--accent); }
    .catalog-facts {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 10px;
      margin: 12px 0 16px;
    }
    .catalog-fact {
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 10px;
      background: #fbfcfe;
      min-width: 0;
    }
    .catalog-fact span { display: block; color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: .04em; }
    .catalog-fact strong, .catalog-fact code {
      display: block;
      margin-top: 4px;
      overflow-wrap: anywhere;
    }
    .catalog-fact code { color: var(--accent); font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
    .origin-badge {
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      padding: 2px 8px;
      font-size: 11px;
      font-weight: 700;
      letter-spacing: .03em;
      text-transform: uppercase;
      white-space: nowrap;
    }
    .origin-badge.default { background: #e5f0fa; color: #1f5f8b; }
    .origin-badge.custom { background: #f1eadf; color: #7a5630; }
    .failure-row { cursor: pointer; }
    .failure-row:hover { background: #f9fafc; }
    .drill-button {
      background: #fff;
      color: var(--accent);
      border-color: #b8ccdf;
      white-space: nowrap;
    }
    .failure-drilldown {
      margin-top: 16px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fbfcfe;
      overflow: hidden;
    }
    .failure-drilldown-head {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: flex-start;
      padding: 14px 16px;
      border-bottom: 1px solid var(--line);
      background: #fff;
    }
    .failure-drilldown-head h3 { margin: 0; font-size: 16px; }
    .thought-stream { padding: 14px 16px; display: grid; gap: 8px; }
    .thought-chunk {
      border-left: 3px solid var(--accent);
      background: #fff;
      padding: 8px 10px;
      white-space: pre-wrap;
    }
    .thought-seq { color: var(--muted); font-size: 12px; margin-bottom: 3px; }
    table { width: 100%; border-collapse: collapse; }
    th, td { text-align: left; border-bottom: 1px solid var(--line); padding: 9px 8px; vertical-align: top; }
    th { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .04em; }
    td:last-child { max-width: 520px; }
    .pill { border-radius: 999px; padding: 3px 8px; font-size: 12px; }
    .pill.match { background: var(--ok-bg); color: var(--ok); }
    .pill.mismatch { background: var(--fail-bg); color: var(--fail); }
    .loading { color: var(--muted); }
    .error { color: var(--fail); white-space: pre-wrap; }
    .modal { display: none; position: fixed; inset: 0; background: rgba(12, 18, 28, .5); padding: 36px; z-index: 10; }
    .modal.open { display: block; }
    .modal-card { background: #101820; color: #e8edf4; max-width: 980px; margin: 0 auto; border-radius: 8px; overflow: hidden; }
    .modal-head { display: flex; justify-content: space-between; align-items: center; padding: 14px 16px; border-bottom: 1px solid #2b394a; }
    button { border: 1px solid #415369; background: #182536; color: #fff; border-radius: 6px; padding: 6px 10px; cursor: pointer; }
    pre { margin: 0; padding: 16px; overflow: auto; max-height: 70vh; white-space: pre-wrap; }
    .foot { margin-top: 18px; color: var(--muted); font-size: 12px; }
    @media (max-width: 900px) {
      main, header { padding-left: 16px; padding-right: 16px; }
      .kpis, .two, .kpi-grid { grid-template-columns: 1fr; }
      .catalog-shell { grid-template-columns: 1fr; }
      .catalog-sidebar { border-right: 0; border-bottom: 1px solid var(--line); }
      .matrix { grid-template-columns: repeat(2, minmax(120px, 1fr)); }
      .provider-row, .bar-row { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <header>
    <h1>Latest Quarterly Financials Eval Canvas</h1>
    <p>Focused on the latest 20-variant run for <code>__SCENARIO_ID__</code>. Each panel runs its SQL in the browser with DuckDB-WASM against the bundled AXP parquet and CSV data.</p>
    <p class="foot">Generated __GENERATED_AT__. Data bundle: <code>docs/reports/__DATA_BASE__</code>.</p>
    <div class="status" id="runtimeStatus">Initializing DuckDB-WASM...</div>
    <nav class="tabbar" aria-label="Report views">
      <button type="button" class="tab-button active" data-tab-target="results">Results Canvas</button>
      <button type="button" class="tab-button" data-tab-target="catalog">Database Catalog</button>
    </nav>
  </header>
  <main>
    <section class="tab-view active" id="resultsView" data-tab-view="results">
      <section class="grid kpis">
        <div class="panel query-panel" tabindex="0" data-query-id="summary">
          <h2>Run Summary</h2>
          <div data-role="panel-body" class="loading">Loading...</div>
        </div>
      </section>

      <section class="grid two">
        <div class="panel query-panel" tabindex="0" data-query-id="provider">
          <h2>Provider Pass Rate</h2>
          <div data-role="panel-body" class="loading">Loading...</div>
        </div>
        <div class="panel query-panel" tabindex="0" data-query-id="failure_fields">
          <h2>Failure Buckets</h2>
          <div data-role="panel-body" class="loading">Loading...</div>
        </div>
      </section>

      <section class="panel query-panel" tabindex="0" data-query-id="matrix" style="margin-top:16px">
        <h2>Ticker x Provider Matrix</h2>
        <div data-role="panel-body" class="loading">Loading...</div>
      </section>

      <section class="grid two" style="margin-top:16px">
        <div class="panel query-panel" tabindex="0" data-query-id="periods">
          <h2>Reported Period Alignment</h2>
          <div data-role="panel-body" class="loading">Loading...</div>
        </div>
        <div class="panel query-panel" tabindex="0" data-query-id="runtime">
          <h2>Runtime And Agent Cost</h2>
          <div data-role="panel-body" class="loading">Loading...</div>
        </div>
      </section>

      <section class="panel query-panel" tabindex="0" data-query-id="failures" style="margin-top:16px">
        <h2>Remaining Failure Details</h2>
        <div data-role="panel-body" class="loading">Loading...</div>
      </section>

      <section class="panel query-panel" tabindex="0" data-query-id="expected_values" style="margin-top:16px">
        <h2>Expected MarketWatch-Style Fixture</h2>
        <div data-role="panel-body" class="loading">Loading...</div>
      </section>
    </section>

    <section class="tab-view" id="catalogView" data-tab-view="catalog">
      <section class="catalog-page query-panel" tabindex="0" data-query-id="schema_overview">
        <h2>Database Catalog</h2>
        <div data-role="panel-body" class="loading">Loading...</div>
      </section>
    </section>
  </main>

  <div class="modal" id="sqlModal" aria-hidden="true">
    <div class="modal-card">
      <div class="modal-head"><strong id="sqlTitle">SQL</strong><button type="button" id="closeModal">Close</button></div>
      <pre id="sqlText"></pre>
    </div>
  </div>
  <script type="application/json" id="queryData">__QUERY_JSON__</script>
  <script type="module">
    import * as duckdb from "https://cdn.jsdelivr.net/npm/@duckdb/duckdb-wasm@latest/+esm";

    const DATA_BASE = "__DATA_BASE__";
    const queries = JSON.parse(document.getElementById("queryData").textContent);
    const supportQueryIds = ["failure_thoughts"];
    const queryResults = {};
    const tableOrigins = {
      runs: { label: "Default AXP", className: "default" },
      tests: { label: "Default AXP", className: "default" },
      messages: { label: "Default AXP", className: "default" },
      agent_events: { label: "Default AXP", className: "default" },
      harness_spans: { label: "Default AXP", className: "default" },
      tool_calls: { label: "Default AXP", className: "default" },
      actual_values: { label: "Custom", className: "custom" },
      expected_values: { label: "Custom", className: "custom" },
      failures: { label: "Custom", className: "custom" },
      metric_comparisons: { label: "Custom", className: "custom" },
      failure_thoughts: { label: "Custom", className: "custom" },
    };
    const statusEl = document.getElementById("runtimeStatus");
    const modal = document.getElementById("sqlModal");
    const sqlText = document.getElementById("sqlText");
    const sqlTitle = document.getElementById("sqlTitle");

    function normalizeValue(value) {
      if (typeof value === "bigint") return Number(value);
      if (value instanceof Date) return value.toISOString();
      return value;
    }

    function normalizeRows(arrowTable) {
      return arrowTable.toArray().map((row) => {
        const out = {};
        for (const [key, value] of Object.entries(row)) out[key] = normalizeValue(value);
        return out;
      });
    }

    function escapeHtml(value) {
      return String(value ?? "").replace(/[&<>"']/g, (char) => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      })[char]);
    }

    function moneyShort(value) {
      const n = Number(value);
      if (!Number.isFinite(n)) return "";
      const sign = n < 0 ? "-" : "";
      const abs = Math.abs(n);
      if (abs >= 1_000_000_000) return `${sign}$${(abs / 1_000_000_000).toFixed(1)}B`;
      if (abs >= 1_000_000) return `${sign}$${(abs / 1_000_000).toFixed(0)}M`;
      return `${sign}$${abs.toLocaleString()}`;
    }

    function renderTable(container, columns, rows) {
      container.className = "";
      const head = columns.map((col) => `<th>${escapeHtml(col.label)}</th>`).join("");
      const body = rows.map((row) => {
        const cells = columns.map((col) => `<td>${col.format ? col.format(row[col.key], row) : escapeHtml(row[col.key])}</td>`).join("");
        return `<tr>${cells}</tr>`;
      }).join("");
      container.innerHTML = `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
    }

    function renderSummary(container, rows) {
      const row = rows[0] || {};
      container.className = "";
      container.innerHTML = `
        <div class="kpi-grid">
          <div class="kpi"><span>Variants</span><strong>${escapeHtml(row.total_variants)}</strong></div>
          <div class="kpi"><span>Passed</span><strong>${escapeHtml(row.passed)}</strong></div>
          <div class="kpi"><span>Failed</span><strong>${escapeHtml(row.failed)}</strong></div>
          <div class="kpi"><span>Pass Rate</span><strong>${escapeHtml(row.pass_rate)}%</strong></div>
        </div>`;
    }

    function renderProvider(container, rows) {
      container.className = "";
      container.innerHTML = rows.map((row) => `
        <div class="provider-row">
          <div><strong>${escapeHtml(row.provider)}</strong><span>${escapeHtml(row.passed)} pass / ${escapeHtml(row.failed)} fail</span></div>
          <div class="rate"><div style="width:${Number(row.pass_rate) || 0}%"></div></div>
          <b>${escapeHtml(row.pass_rate)}%</b>
        </div>`).join("");
    }

    function renderFailureFields(container, rows) {
      container.className = "";
      const max = Math.max(0, ...rows.map((row) => Number(row.failures) || 0));
      container.innerHTML = rows.map((row) => {
        const value = Number(row.failures) || 0;
        const width = max === 0 ? 0 : Math.max(2, Math.min(100, value / max * 100));
        return `<div class="bar-row"><span>${escapeHtml(row.failure_bucket)}</span><div class="bar-track"><div class="bar-fill fail" style="width:${width}%"></div></div><strong>${value}</strong></div>`;
      }).join("") || "<p>No failures in this run.</p>";
    }

    function renderMatrix(container, rows) {
      container.className = "";
      container.innerHTML = `<div class="matrix">${rows.map((row) => {
        const statusClass = row.status === "pass" ? "pass" : "fail";
        return `<div class="matrix-cell ${statusClass}"><span>${escapeHtml(row.ticker)}</span><small>${escapeHtml(row.provider)}</small><b>${escapeHtml(row.status)}</b></div>`;
      }).join("")}</div>`;
    }

    function failureKey(row) {
      return [row.variant_id, row.test_name, row.field || "", row.message || ""].join("||");
    }

    function renderFailures(container, rows) {
      container.className = "";
      const tableRows = rows.map((row) => {
        const key = escapeHtml(failureKey(row));
        return `<tr class="failure-row" data-failure-key="${key}">
          <td>${escapeHtml(row.ticker)}</td>
          <td>${escapeHtml(row.provider)}</td>
          <td>${escapeHtml(row.field)}</td>
          <td>${escapeHtml(row.actual)}</td>
          <td>${escapeHtml(row.expected)}</td>
          <td>${escapeHtml(row.message)}</td>
          <td><button type="button" class="drill-button" data-failure-key="${key}">Inspect</button></td>
        </tr>`;
      }).join("");
      container.innerHTML = `
        <table>
          <thead><tr><th>Ticker</th><th>Provider</th><th>Field</th><th>Actual</th><th>Expected</th><th>Message</th><th></th></tr></thead>
          <tbody>${tableRows}</tbody>
        </table>
        <div class="failure-drilldown" data-role="failure-drilldown">
          <div class="failure-drilldown-head">
            <div>
              <h3>Agent Thinking Before Failure</h3>
              <div class="catalog-meta">Select a failure row to inspect the final thought chunks recorded before the answer was submitted.</div>
            </div>
            <button type="button" data-show-sql="failure_thoughts">View SQL</button>
          </div>
          <div class="thought-stream"><div class="catalog-meta">No failure selected.</div></div>
        </div>`;
      container.querySelectorAll("[data-failure-key]").forEach((element) => {
        element.addEventListener("click", (event) => {
          event.stopPropagation();
          const key = element.dataset.failureKey;
          const failure = rows.find((row) => failureKey(row) === key);
          renderFailureDrilldown(container, failure);
        });
      });
      container.querySelector("[data-show-sql]").addEventListener("click", (event) => {
        event.stopPropagation();
        openQuery("failure_thoughts");
      });
    }

    function renderFailureDrilldown(container, failure) {
      const target = container.querySelector("[data-role=failure-drilldown]");
      if (!target || !failure) return;
      const key = failureKey(failure);
      const thoughts = (queryResults.failure_thoughts || []).filter((row) => failureKey(row) === key);
      const stream = thoughts.length
        ? thoughts.map((row) => `<div class="thought-chunk"><div class="thought-seq">agent_events.seq ${escapeHtml(row.seq)}</div>${escapeHtml(row.thought_chunk)}</div>`).join("")
        : `<div class="loading">No agent thought chunks were captured for this failure.</div>`;
      target.innerHTML = `
        <div class="failure-drilldown-head">
          <div>
            <h3>Agent Thinking Before Failure</h3>
            <div class="catalog-meta">${escapeHtml(failure.variant_id)} · ${escapeHtml(failure.test_name)} · ${escapeHtml(failure.field || "failure")}</div>
            <div class="catalog-meta">${escapeHtml(failure.message)}</div>
          </div>
          <button type="button" data-show-sql="failure_thoughts">View SQL</button>
        </div>
        <div class="thought-stream">${stream}</div>`;
      target.querySelector("[data-show-sql]").addEventListener("click", (event) => {
        event.stopPropagation();
        openQuery("failure_thoughts");
      });
    }

    function renderSchemaOverview(container, rows) {
      container.className = "";
      const groups = new Map();
      for (const row of rows) {
        if (!groups.has(row.table_name)) groups.set(row.table_name, []);
        groups.get(row.table_name).push(row);
      }
      const entries = [...groups.entries()];
      container.innerHTML = `
        <div class="catalog-shell">
          <aside class="catalog-sidebar">
            <div class="catalog-sidebar-head"><span>Objects</span><strong>${entries.length}</strong></div>
            <div class="catalog-table-list">
              ${entries.map(([tableName, columns], index) => {
                const meta = columns[0] || {};
                const originClass = meta.origin_class || tableOrigins[tableName]?.className || "custom";
                const originLabel = meta.origin || tableOrigins[tableName]?.label || "Custom";
                const objectKind = meta.object_kind || "table";
                return `
                <button type="button" class="catalog-table-button ${index === 0 ? "active" : ""}" data-catalog-table="${escapeHtml(tableName)}">
                  <span><strong>${escapeHtml(tableName)}</strong><br><small>${escapeHtml(objectKind)} · ${columns.length} cols</small></span>
                  <span class="origin-badge ${escapeHtml(originClass)}">${escapeHtml(originLabel)}</span>
                </button>`;
              }).join("")}
            </div>
          </aside>
          <div class="catalog-detail">
            ${entries.map(([tableName, columns], index) => {
              const meta = columns[0] || {};
              const originClass = meta.origin_class || tableOrigins[tableName]?.className || "custom";
              const originLabel = meta.origin || tableOrigins[tableName]?.label || "Custom";
              const objectKind = meta.object_kind || "table";
              const sourcePath = meta.source_path || "";
              const provenance = meta.provenance || "";
              const body = columns.map((column) => `<tr><td>${escapeHtml(column.column_name)}</td><td class="catalog-type">${escapeHtml(column.column_type)}</td></tr>`).join("");
              return `
                <section class="catalog-detail-section ${index === 0 ? "active" : ""}" data-catalog-detail="${escapeHtml(tableName)}">
                  <div class="catalog-detail-head">
                    <div>
                      <h3>${escapeHtml(tableName)}</h3>
                      <div class="catalog-meta">main.${escapeHtml(tableName)} · ${columns.length} columns</div>
                    </div>
                    <span class="origin-badge ${escapeHtml(originClass)}">${escapeHtml(originLabel)}</span>
                    <button type="button" data-show-sql="schema_overview">View SQL</button>
                  </div>
                  <div class="catalog-facts">
                    <div class="catalog-fact"><span>Object Type</span><strong>${escapeHtml(objectKind)}</strong></div>
                    <div class="catalog-fact"><span>Source</span><code>${escapeHtml(sourcePath)}</code></div>
                    <div class="catalog-fact"><span>Provenance</span><strong>${escapeHtml(provenance)}</strong></div>
                  </div>
                  <table><thead><tr><th>Column</th><th>Type</th></tr></thead><tbody>${body}</tbody></table>
                </section>`;
            }).join("")}
          </div>
        </div>`;
      container.querySelectorAll("[data-catalog-table]").forEach((button) => {
        button.addEventListener("click", (event) => {
          event.stopPropagation();
          const tableName = button.dataset.catalogTable;
          container.querySelectorAll("[data-catalog-table]").forEach((item) => item.classList.toggle("active", item === button));
          container.querySelectorAll("[data-catalog-detail]").forEach((detail) => {
            detail.classList.toggle("active", detail.dataset.catalogDetail === tableName);
          });
        });
      });
      container.querySelectorAll("[data-show-sql]").forEach((button) => {
        button.addEventListener("click", (event) => {
          event.stopPropagation();
          openQuery(button.dataset.showSql);
        });
      });
    }

    const renderers = {
      summary: renderSummary,
      provider: renderProvider,
      failure_fields: renderFailureFields,
      matrix: renderMatrix,
      schema_overview: renderSchemaOverview,
      periods: (container, rows) => renderTable(container, [
        { key: "ticker", label: "Ticker" },
        { key: "provider", label: "Provider" },
        { key: "actual_period", label: "Actual" },
        { key: "expected_period", label: "Expected" },
        { key: "period_status", label: "Status", format: (value) => `<span class="pill ${escapeHtml(value)}">${escapeHtml(value)}</span>` },
      ], rows),
      runtime: (container, rows) => renderTable(container, [
        { key: "provider", label: "Provider" },
        { key: "avg_duration_sec", label: "Avg Duration", format: (value) => `${escapeHtml(value)}s` },
        { key: "total_agent_cost_usd", label: "Total Cost", format: (value) => `$${Number(value).toFixed(4)}` },
        { key: "avg_turns", label: "Avg Turns" },
      ], rows),
      failures: renderFailures,
      expected_values: (container, rows) => renderTable(container, [
        { key: "ticker", label: "Ticker" },
        { key: "fiscal_period", label: "Period", format: (value, row) => `FY${escapeHtml(row.fiscal_year)} ${escapeHtml(value)}` },
        { key: "report_period", label: "Report Date" },
        { key: "revenue", label: "Revenue", format: moneyShort },
        { key: "net_income", label: "Net Income", format: moneyShort },
        { key: "capital_expenditures", label: "CapEx", format: moneyShort },
        { key: "free_cash_flow", label: "FCF", format: moneyShort },
      ], rows),
    };

    async function initDuckDB() {
      const bundle = await duckdb.selectBundle(duckdb.getJsDelivrBundles());
      const workerUrl = URL.createObjectURL(new Blob([`importScripts("${bundle.mainWorker}");`], { type: "text/javascript" }));
      const worker = new Worker(workerUrl);
      const db = new duckdb.AsyncDuckDB(new duckdb.ConsoleLogger(), worker);
      await db.instantiate(bundle.mainModule, bundle.pthreadWorker);
      URL.revokeObjectURL(workerUrl);
      await registerDataFiles(db);
      const conn = await db.connect();
      await conn.query("CREATE OR REPLACE VIEW runs AS SELECT * FROM read_parquet('runs.parquet')");
      await conn.query("CREATE OR REPLACE VIEW tests AS SELECT * FROM read_parquet('tests.parquet')");
      await conn.query("CREATE OR REPLACE VIEW messages AS SELECT * FROM read_parquet('messages.parquet')");
      await conn.query("CREATE OR REPLACE VIEW agent_events AS SELECT * FROM read_parquet('agent_events.parquet')");
      await conn.query("CREATE OR REPLACE VIEW harness_spans AS SELECT * FROM read_parquet('harness_spans.parquet')");
      await conn.query("CREATE OR REPLACE VIEW tool_calls AS SELECT * FROM read_parquet('tool_calls.parquet')");
      await conn.query("CREATE OR REPLACE VIEW actual_values AS SELECT * FROM read_csv_auto('actual_values.csv')");
      await conn.query("CREATE OR REPLACE VIEW expected_values AS SELECT * FROM read_csv_auto('expected_values.csv')");
      await conn.query("CREATE OR REPLACE VIEW failures AS SELECT * FROM read_csv_auto('failures.csv')");
      await conn.query("CREATE OR REPLACE VIEW metric_comparisons AS SELECT * FROM read_csv_auto('metric_comparisons.csv')");
      await conn.query(`
        CREATE OR REPLACE VIEW failure_thoughts AS
        WITH ranked_thoughts AS (
          SELECT
            eval_id,
            seq,
            json_extract_string(raw_json, '$.params.update.content.text') AS thought_chunk,
            row_number() OVER (PARTITION BY eval_id ORDER BY seq DESC) AS thought_rank
          FROM agent_events
          WHERE kind = 'agent_thought_chunk'
        )
        SELECT
          f.variant_id,
          f.ticker,
          f.provider,
          f.test_name,
          f.field,
          f.message,
          t.seq,
          t.thought_chunk
        FROM failures f
        JOIN runs r
          ON r.run_group_id = f.run_group_id
         AND r.variant_id = f.variant_id
        JOIN ranked_thoughts t
          ON t.eval_id = r.eval_id
        WHERE t.thought_rank <= 24
          AND t.thought_chunk IS NOT NULL
          AND trim(t.thought_chunk) <> ''
      `);
      return conn;
    }

    async function registerDataFiles(db) {
      const files = [
        ["runs.parquet", "__DATA_BASE__/parquet/runs.parquet"],
        ["tests.parquet", "__DATA_BASE__/parquet/tests.parquet"],
        ["messages.parquet", "__DATA_BASE__/parquet/messages.parquet"],
        ["agent_events.parquet", "__DATA_BASE__/parquet/agent_events.parquet"],
        ["harness_spans.parquet", "__DATA_BASE__/parquet/harness_spans.parquet"],
        ["tool_calls.parquet", "__DATA_BASE__/parquet/tool_calls.parquet"],
        ["actual_values.csv", "__DATA_BASE__/actual_values.csv"],
        ["expected_values.csv", "__DATA_BASE__/expected_values.csv"],
        ["failures.csv", "__DATA_BASE__/failures.csv"],
        ["metric_comparisons.csv", "__DATA_BASE__/metric_comparisons.csv"],
      ];
      for (const [name, path] of files) {
        const url = new URL(path, window.location.href).toString();
        await db.registerFileURL(name, url, duckdb.DuckDBDataProtocol.HTTP, false);
      }
    }

    async function renderPanel(conn, panel) {
      const id = panel.dataset.queryId;
      const body = panel.querySelector('[data-role="panel-body"]');
      try {
        const result = await conn.query(queries[id]);
        const rows = normalizeRows(result);
        queryResults[id] = rows;
        renderers[id](body, rows);
      } catch (error) {
        body.className = "error";
        body.textContent = error.stack || String(error);
      }
    }

    function openQuery(id) {
      sqlTitle.textContent = id;
      sqlText.textContent = queries[id] || "";
      modal.classList.add("open");
      modal.setAttribute("aria-hidden", "false");
    }

    document.querySelectorAll("[data-tab-target]").forEach((button) => {
      button.addEventListener("click", () => {
        const target = button.dataset.tabTarget;
        document.querySelectorAll("[data-tab-target]").forEach((item) => item.classList.toggle("active", item === button));
        document.querySelectorAll("[data-tab-view]").forEach((view) => view.classList.toggle("active", view.dataset.tabView === target));
      });
    });

    document.querySelectorAll("[data-query-id]").forEach((panel) => {
      panel.addEventListener("click", (event) => {
        if (event.target.closest("[data-catalog-table], [data-show-sql]")) return;
        openQuery(panel.dataset.queryId);
      });
      panel.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          openQuery(panel.dataset.queryId);
        }
      });
    });
    document.getElementById("closeModal").addEventListener("click", () => {
      modal.classList.remove("open");
      modal.setAttribute("aria-hidden", "true");
    });
    modal.addEventListener("click", (event) => {
      if (event.target === modal) {
        modal.classList.remove("open");
        modal.setAttribute("aria-hidden", "true");
      }
    });

    try {
      const conn = await initDuckDB();
      for (const queryId of supportQueryIds) {
        const result = await conn.query(queries[queryId]);
        queryResults[queryId] = normalizeRows(result);
      }
      const panels = [...document.querySelectorAll("[data-query-id]")];
      await Promise.all(panels.map((panel) => renderPanel(conn, panel)));
      statusEl.textContent = `DuckDB-WASM queried ${panels.length} panels from bundled parquet/CSV data.`;
    } catch (error) {
      statusEl.className = "status error";
      statusEl.textContent = error.stack || String(error);
    }
  </script>
</body>
</html>
"""
    replacements = {
        "__SCENARIO_ID__": html.escape(SCENARIO_ID),
        "__GENERATED_AT__": html.escape(generated_at),
        "__DATA_BASE__": data_base,
        "__QUERY_JSON__": script_json(queries),
    }
    for key, value in replacements.items():
        html_text = html_text.replace(key, value)
    return html_text


def render_report(output: Path, queries: dict[str, str], rows: dict[str, list[dict[str, Any]]]) -> None:
    summary = rows["summary"][0]
    provider_rows = rows["provider"]
    matrix_rows = rows["matrix"]
    failure_field_rows = rows["failure_fields"]
    failure_rows = rows["failures"]
    runtime_rows = rows["runtime"]
    period_rows = rows["periods"]
    expected_rows = rows["expected_values"]

    max_failures = max([row["failures"] for row in failure_field_rows] or [0])
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    pass_rate = summary["pass_rate"]

    provider_html = []
    for row in provider_rows:
        provider_html.append(
            f"""
            <div class="provider-row">
              <div>
                <strong>{html.escape(row['provider'])}</strong>
                <span>{row['passed']} pass / {row['failed']} fail</span>
              </div>
              <div class="rate"><div style="width:{row['pass_rate']}%"></div></div>
              <b>{row['pass_rate']}%</b>
            </div>
            """
        )

    matrix_html = []
    for row in matrix_rows:
        status_class = "pass" if row["status"] == "pass" else "fail"
        matrix_html.append(
            f'<div class="matrix-cell {status_class}"><span>{html.escape(row["ticker"])}</span>'
            f'<small>{html.escape(row["provider"])}</small><b>{html.escape(row["status"])}</b></div>'
        )

    failure_bars = "\n".join(
        render_bar(row["failure_bucket"], float(row["failures"]), float(max_failures), "fail") for row in failure_field_rows
    )

    failure_table = "\n".join(
        f"<tr><td>{html.escape(str(row['ticker']))}</td><td>{html.escape(str(row['provider']))}</td>"
        f"<td>{html.escape(str(row['field']))}</td><td>{html.escape(str(row['actual']))}</td>"
        f"<td>{html.escape(str(row['expected']))}</td><td>{html.escape(str(row['message']))}</td></tr>"
        for row in failure_rows
    )

    runtime_table = "\n".join(
        f"<tr><td>{html.escape(row['provider'])}</td><td>{row['avg_duration_sec']}s</td>"
        f"<td>${row['total_agent_cost_usd']:.4f}</td><td>{row['avg_turns']}</td></tr>"
        for row in runtime_rows
    )

    period_table = "\n".join(
        f"<tr><td>{html.escape(row['ticker'])}</td><td>{html.escape(row['provider'])}</td>"
        f"<td>{html.escape(str(row['actual_period']))}</td><td>{html.escape(str(row['expected_period']))}</td>"
        f"<td><span class='pill {html.escape(row['period_status'])}'>{html.escape(row['period_status'])}</span></td></tr>"
        for row in period_rows
    )

    expected_table = "\n".join(
        f"<tr><td>{html.escape(row['ticker'])}</td><td>FY{row['fiscal_year']} {html.escape(row['fiscal_period'])}</td>"
        f"<td>{html.escape(str(row['report_period']))}</td><td>{money_short(row['revenue'])}</td>"
        f"<td>{money_short(row['net_income'])}</td><td>{money_short(row['capital_expenditures'])}</td>"
        f"<td>{money_short(row['free_cash_flow'])}</td></tr>"
        for row in expected_rows
    )

    query_json = script_json(queries)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Latest Quarterly Financials Eval Canvas</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f6f7f9;
      --ink: #18212f;
      --muted: #657083;
      --panel: #ffffff;
      --line: #d9dee7;
      --ok: #176b5b;
      --ok-bg: #dff3ee;
      --fail: #b13b32;
      --fail-bg: #f7dfdd;
      --accent: #1f5f8b;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font: 14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    header {{
      padding: 28px 32px 18px;
      border-bottom: 1px solid var(--line);
      background: #fff;
    }}
    h1 {{ margin: 0 0 8px; font-size: 28px; letter-spacing: 0; }}
    h2 {{ margin: 0 0 14px; font-size: 16px; }}
    p {{ margin: 0; color: var(--muted); max-width: 940px; }}
    main {{ padding: 24px 32px 40px; }}
    .grid {{ display: grid; gap: 16px; }}
    .kpis {{ grid-template-columns: repeat(4, minmax(140px, 1fr)); margin-bottom: 16px; }}
    .two {{ grid-template-columns: minmax(0, 1.1fr) minmax(0, .9fr); }}
    .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 18px;
      box-shadow: 0 1px 2px rgba(20, 31, 46, .05);
      cursor: pointer;
    }}
    .panel:focus {{ outline: 2px solid var(--accent); outline-offset: 2px; }}
    .kpi span, .subtle {{ color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .04em; }}
    .kpi strong {{ display: block; margin-top: 6px; font-size: 30px; }}
    .provider-row {{ display: grid; grid-template-columns: 190px 1fr 56px; align-items: center; gap: 12px; margin: 12px 0; }}
    .provider-row span {{ display: block; color: var(--muted); font-size: 12px; margin-top: 2px; }}
    .rate, .bar-track {{ height: 10px; background: #edf0f4; border-radius: 999px; overflow: hidden; }}
    .rate div, .bar-fill {{ height: 100%; background: var(--ok); }}
    .bar-fill.fail {{ background: var(--fail); }}
    .matrix {{ display: grid; grid-template-columns: repeat(5, minmax(120px, 1fr)); gap: 10px; }}
    .matrix-cell {{ border: 1px solid var(--line); border-radius: 6px; padding: 10px; min-height: 74px; }}
    .matrix-cell.pass {{ background: var(--ok-bg); border-color: #abd7cc; }}
    .matrix-cell.fail {{ background: var(--fail-bg); border-color: #e3aca7; }}
    .matrix-cell span, .matrix-cell b {{ display: block; }}
    .matrix-cell small {{ color: var(--muted); display: block; min-height: 34px; }}
    .bar-row {{ display: grid; grid-template-columns: 150px 1fr 36px; gap: 10px; align-items: center; margin: 10px 0; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ text-align: left; border-bottom: 1px solid var(--line); padding: 9px 8px; vertical-align: top; }}
    th {{ color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .04em; }}
    td:last-child {{ max-width: 520px; }}
    .pill {{ border-radius: 999px; padding: 3px 8px; font-size: 12px; }}
    .pill.match {{ background: var(--ok-bg); color: var(--ok); }}
    .pill.mismatch {{ background: var(--fail-bg); color: var(--fail); }}
    .modal {{ display: none; position: fixed; inset: 0; background: rgba(12, 18, 28, .5); padding: 36px; z-index: 10; }}
    .modal.open {{ display: block; }}
    .modal-card {{ background: #101820; color: #e8edf4; max-width: 980px; margin: 0 auto; border-radius: 8px; overflow: hidden; }}
    .modal-head {{ display: flex; justify-content: space-between; align-items: center; padding: 14px 16px; border-bottom: 1px solid #2b394a; }}
    button {{ border: 1px solid #415369; background: #182536; color: #fff; border-radius: 6px; padding: 6px 10px; cursor: pointer; }}
    pre {{ margin: 0; padding: 16px; overflow: auto; max-height: 70vh; white-space: pre-wrap; }}
    .foot {{ margin-top: 18px; color: var(--muted); font-size: 12px; }}
    @media (max-width: 900px) {{
      main, header {{ padding-left: 16px; padding-right: 16px; }}
      .kpis, .two {{ grid-template-columns: 1fr; }}
      .matrix {{ grid-template-columns: repeat(2, minmax(120px, 1fr)); }}
      .provider-row, .bar-row {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>Latest Quarterly Financials Eval Canvas</h1>
    <p>Focused on the latest 20-variant run for <code>{html.escape(SCENARIO_ID)}</code>. Click any panel to inspect the DuckDB SQL behind that view.</p>
    <p class="foot">Generated {generated_at}. AXP parquet dataset: <code>.axp/downloads/{REPORT_RUN_ID}</code>. Sidecar values: <code>{html.escape(rel(DATA_DIR))}</code>.</p>
  </header>
  <main>
    <section class="grid kpis">
      <div {panel_attrs('summary')}><div class="kpi"><span>Variants</span><strong>{summary['total_variants']}</strong></div></div>
      <div {panel_attrs('summary')}><div class="kpi"><span>Passed</span><strong>{summary['passed']}</strong></div></div>
      <div {panel_attrs('summary')}><div class="kpi"><span>Failed</span><strong>{summary['failed']}</strong></div></div>
      <div {panel_attrs('summary')}><div class="kpi"><span>Pass Rate</span><strong>{pct(pass_rate)}</strong></div></div>
    </section>

    <section class="grid two">
      <div {panel_attrs('provider')}>
        <h2>Provider Pass Rate</h2>
        {''.join(provider_html)}
      </div>
      <div {panel_attrs('failure_fields')}>
        <h2>Failure Buckets</h2>
        {failure_bars or '<p>No failures in this run.</p>'}
      </div>
    </section>

    <section {panel_attrs('matrix')} style="margin-top:16px">
      <h2>Ticker x Provider Matrix</h2>
      <div class="matrix">{''.join(matrix_html)}</div>
    </section>

    <section class="grid two" style="margin-top:16px">
      <div {panel_attrs('periods')}>
        <h2>Reported Period Alignment</h2>
        <table><thead><tr><th>Ticker</th><th>Provider</th><th>Actual</th><th>Expected</th><th>Status</th></tr></thead><tbody>{period_table}</tbody></table>
      </div>
      <div {panel_attrs('runtime')}>
        <h2>Runtime And Agent Cost</h2>
        <table><thead><tr><th>Provider</th><th>Avg Duration</th><th>Total Cost</th><th>Avg Turns</th></tr></thead><tbody>{runtime_table}</tbody></table>
      </div>
    </section>

    <section {panel_attrs('failures')} style="margin-top:16px">
      <h2>Remaining Failure Details</h2>
      <table><thead><tr><th>Ticker</th><th>Provider</th><th>Field</th><th>Actual</th><th>Expected</th><th>Message</th></tr></thead><tbody>{failure_table}</tbody></table>
    </section>

    <section {panel_attrs('expected_values')} style="margin-top:16px">
      <h2>Expected MarketWatch-Style Fixture</h2>
      <table><thead><tr><th>Ticker</th><th>Period</th><th>Report Date</th><th>Revenue</th><th>Net Income</th><th>CapEx</th><th>FCF</th></tr></thead><tbody>{expected_table}</tbody></table>
    </section>
  </main>

  <div class="modal" id="sqlModal" aria-hidden="true">
    <div class="modal-card">
      <div class="modal-head"><strong id="sqlTitle">SQL</strong><button type="button" id="closeModal">Close</button></div>
      <pre id="sqlText"></pre>
    </div>
  </div>
  <script type="application/json" id="queryData">{query_json}</script>
  <script>
    const queries = JSON.parse(document.getElementById('queryData').textContent);
    const modal = document.getElementById('sqlModal');
    const sqlText = document.getElementById('sqlText');
    const sqlTitle = document.getElementById('sqlTitle');
    function openQuery(id) {{
      sqlTitle.textContent = id;
      sqlText.textContent = queries[id] || '';
      modal.classList.add('open');
      modal.setAttribute('aria-hidden', 'false');
    }}
    document.querySelectorAll('[data-query-id]').forEach((panel) => {{
      panel.addEventListener('click', () => openQuery(panel.dataset.queryId));
      panel.addEventListener('keydown', (event) => {{
        if (event.key === 'Enter' || event.key === ' ') {{
          event.preventDefault();
          openQuery(panel.dataset.queryId);
        }}
      }});
    }});
    document.getElementById('closeModal').addEventListener('click', () => {{
      modal.classList.remove('open');
      modal.setAttribute('aria-hidden', 'true');
    }});
    modal.addEventListener('click', (event) => {{
      if (event.target === modal) {{
        modal.classList.remove('open');
        modal.setAttribute('aria-hidden', 'true');
      }}
    }});
  </script>
</body>
</html>
""",
        encoding="utf-8",
    )


def build_report(output: Path = DEFAULT_OUTPUT) -> None:
    variants = discover_latest_variants()
    export_axp_parquet(variants)
    copy_parquet_into_report_bundle()
    expected = extract_expected_fixture()
    paths = write_sidecar_csvs(variants, expected)
    queries = query_catalog(paths)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_live_report_html(queries, generated_at), encoding="utf-8")


def run_self_test() -> None:
    assert provider_for_variant("aapl-financialdatasets-rest") == "Financial Datasets"
    assert provider_for_variant("aapl-sec-edgar") == "SEC EDGAR"
    assert ticker_for_variant("tsla-sec-edgar") == "TSLA"
    parsed = parse_failure("net_income mismatch: 491000000 not within 5000000 of 477000000\n")
    assert parsed == [
        {
            "field": "net_income",
            "actual": "491000000",
            "expected": "477000000",
            "message": "net_income mismatch: 491000000 not within 5000000 of 477000000",
        }
    ]
    parsed = parse_failure("report_period mismatch: 2025-10-31 != 2026-01-31\n")
    assert parsed[0]["field"] == "report_period"
    assert parsed[0]["actual"] == "2025-10-31"
    assert parsed[0]["expected"] == "2026-01-31"
    expected = extract_expected_fixture()
    assert len(expected) == 10
    assert expected["tsla"]["financials_usd"]["net_income"] == 477000000
    assert expected["meta"]["financials_usd"]["capital_expenditures"] == 18997000000
    encoded = script_json({"summary": "SELECT 1 AS x;"})
    assert "&quot;" not in encoded
    assert json.loads(encoded)["summary"] == "SELECT 1 AS x;"
    catalog = query_catalog(
        {
            "actual": REPO_ROOT / "actual_values.csv",
            "expected": REPO_ROOT / "expected_values.csv",
            "comparisons": REPO_ROOT / "metric_comparisons.csv",
            "failures": REPO_ROOT / "failures.csv",
        }
    )
    assert "largest_deltas" not in catalog
    assert "failure_thoughts" in catalog
    assert "FROM failure_thoughts" in catalog["failure_thoughts"]
    assert "schema_overview" in catalog
    assert "pragma_table_info('runs')" in catalog["schema_overview"]
    assert "pragma_table_info('failures')" in catalog["schema_overview"]
    assert "pragma_table_info('failure_thoughts')" in catalog["schema_overview"]
    assert "object_kind" in catalog["schema_overview"]
    assert "source_path" in catalog["schema_overview"]
    assert "docs/reports/data/latest-quarter-run-20/parquet/runs.parquet" in catalog["schema_overview"]
    assert "docs/reports/data/latest-quarter-run-20/actual_values.csv" in catalog["schema_overview"]
    live_html = render_live_report_html(catalog, "2026-05-15 00:00 UTC")
    assert 'data-tab-target="results"' in live_html
    assert 'data-tab-target="catalog"' in live_html
    assert 'id="catalogView"' in live_html
    assert "Database Catalog" in live_html
    assert "catalog-shell" in live_html
    assert "catalog-sidebar" in live_html
    assert "catalog-detail" in live_html
    assert "@duckdb/duckdb-wasm" in live_html
    assert "conn.query" in live_html
    assert "registerFileURL" in live_html
    assert "data/latest-quarter-run-20/parquet/runs.parquet" in live_html
    assert "read_parquet('runs.parquet')" in live_html
    assert "data-role=\"panel-body\"" in live_html
    assert "Database Catalog" in live_html
    assert "schema_overview" in live_html
    assert "renderSchemaOverview" in live_html
    assert "tableOrigins" in live_html
    assert "renderFailures" in live_html
    assert "renderFailureDrilldown" in live_html
    assert "data-failure-key" in live_html
    assert "Agent Thinking Before Failure" in live_html
    assert "CREATE OR REPLACE VIEW failure_thoughts AS" in live_html
    assert "agent_thought_chunk" in live_html
    assert "json_extract_string" in live_html
    assert "Object Type" in live_html
    assert "Provenance" in live_html
    assert "source_path" in live_html
    assert "Default AXP" in live_html
    assert "Custom" in live_html
    assert ".origin-badge.default" in live_html
    assert ".origin-badge.custom" in live_html
    assert 'runs: { label: "Default AXP", className: "default" }' in live_html
    assert 'actual_values: { label: "Custom", className: "custom" }' in live_html
    assert "function renderTable(container, columns, rows) {\n      container.className = \"\";" in live_html
    variants = discover_latest_variants()
    assert len(variants) == 20
    assert {item["run"]["status"] for item in variants} == {"pass", "fail"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        run_self_test()
        return 0

    build_report(args.output.resolve())
    print(args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
