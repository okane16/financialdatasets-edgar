python3 - <<'PY'
import json
import math
import os
import sys

# Static source-truth fixture generated on 2026-05-15 with
# scripts/inspect-latest-sec-financials.py and then hard-coded here.
expected_by_ticker = {
    "aapl": {
        "ticker": "AAPL",
        "fiscal_year": 2026,
        "fiscal_period": "Q2",
        "report_period": "2026-03-28",
        "financials_usd": {
            "revenue": 111184000000,
            "net_income": 29578000000,
            "total_assets": 371082000000,
            "operating_cash_flow": 28702000000,
            "capital_expenditures": 1971000000,
            "free_cash_flow": 26731000000,
        },
    },
    "cost": {
        "ticker": "COST",
        "fiscal_year": 2026,
        "fiscal_period": "Q2",
        "report_period": "2026-02-15",
        "financials_usd": {
            "revenue": 69597000000,
            "net_income": 2035000000,
            "total_assets": 83639000000,
            "operating_cash_flow": 2996000000,
            "capital_expenditures": 1289000000,
            "free_cash_flow": 1707000000,
        },
    },
    "googl": {
        "ticker": "GOOGL",
        "fiscal_year": 2026,
        "fiscal_period": "Q1",
        "report_period": "2026-03-31",
        "financials_usd": {
            "revenue": 109896000000,
            "net_income": 62578000000,
            "total_assets": 703919000000,
            "operating_cash_flow": 45790000000,
            "capital_expenditures": 35674000000,
            "free_cash_flow": 10116000000,
        },
    },
    "jnj": {
        "ticker": "JNJ",
        "fiscal_year": 2026,
        "fiscal_period": "Q1",
        "report_period": "2026-03-29",
        "financials_usd": {
            "revenue": 24062000000,
            "net_income": 5235000000,
            "total_assets": 200894000000,
            "operating_cash_flow": 2514000000,
            "capital_expenditures": 1049000000,
            "free_cash_flow": 1465000000,
        },
    },
    "meta": {
        "ticker": "META",
        "fiscal_year": 2026,
        "fiscal_period": "Q1",
        "report_period": "2026-03-31",
        "financials_usd": {
            "revenue": 56311000000,
            "net_income": 26773000000,
            "total_assets": 395250000000,
            "operating_cash_flow": 32226000000,
            "capital_expenditures": 18997000000,
            "free_cash_flow": 13229000000,
        },
    },
    "msft": {
        "ticker": "MSFT",
        "fiscal_year": 2026,
        "fiscal_period": "Q3",
        "report_period": "2026-03-31",
        "financials_usd": {
            "revenue": 82886000000,
            "net_income": 31778000000,
            "total_assets": 694228000000,
            "operating_cash_flow": 46679000000,
            "capital_expenditures": 30876000000,
            "free_cash_flow": 15803000000,
        },
    },
    "pg": {
        "ticker": "PG",
        "fiscal_year": 2026,
        "fiscal_period": "Q3",
        "report_period": "2026-03-31",
        "financials_usd": {
            "revenue": 21235000000,
            "net_income": 3932000000,
            "total_assets": 128378000000,
            "operating_cash_flow": 4045000000,
            "capital_expenditures": 1019000000,
            "free_cash_flow": 3026000000,
        },
    },
    "tsla": {
        "ticker": "TSLA",
        "fiscal_year": 2026,
        "fiscal_period": "Q1",
        "report_period": "2026-03-31",
        "financials_usd": {
            "revenue": 22387000000,
            "net_income": 477000000,
            "total_assets": 143724000000,
            "operating_cash_flow": 3937000000,
            "capital_expenditures": 2493000000,
            "free_cash_flow": 1444000000,
        },
    },
    "wmt": {
        "ticker": "WMT",
        "fiscal_year": 2026,
        "fiscal_period": "Q4",
        "report_period": "2026-01-31",
        "financials_usd": {
            "revenue": 190656000000,
            "net_income": 4237000000,
            "total_assets": 284668000000,
            "operating_cash_flow": 14113000000,
            "capital_expenditures": 8015000000,
            "free_cash_flow": 6098000000,
        },
    },
    "xom": {
        "ticker": "XOM",
        "fiscal_year": 2026,
        "fiscal_period": "Q1",
        "report_period": "2026-03-31",
        "financials_usd": {
            "revenue": 85138000000,
            "net_income": 4183000000,
            "total_assets": 464410000000,
            "operating_cash_flow": 8705000000,
            "capital_expenditures": 6470000000,
            "free_cash_flow": 2235000000,
        },
    },
}

def fail(message):
    print(message, file=sys.stderr)
    sys.exit(1)

variant_id = os.environ["AXP_VARIANT_ID"]
ticker_key = variant_id.split("-", 1)[0]
expected = expected_by_ticker[ticker_key]
with open("/workspace/answer.json", "r", encoding="utf-8") as f:
    got = json.load(f)

if got.get("ticker") != expected["ticker"]:
    fail(f"ticker mismatch: {got.get('ticker')} != {expected['ticker']}")
if got.get("fiscal_year") != expected["fiscal_year"]:
    fail(f"fiscal_year mismatch: {got.get('fiscal_year')} != {expected['fiscal_year']}")
if got.get("report_period") != expected["report_period"]:
    fail(f"report_period mismatch: {got.get('report_period')} != {expected['report_period']}")

financials = got.get("financials_usd", {})
for key in ["revenue", "net_income", "total_assets", "operating_cash_flow", "capital_expenditures", "free_cash_flow"]:
    actual = financials.get(key)
    target = expected["financials_usd"][key]
    tolerance = max(abs(target) * 0.002, 5_000_000)
    if actual is None or abs(actual - target) > tolerance:
        fail(f"{key} mismatch: {actual} not within {tolerance} of {target}")

computed_fcf = financials["operating_cash_flow"] - financials["capital_expenditures"]
if abs(financials["free_cash_flow"] - computed_fcf) > 1:
    fail("free_cash_flow does not equal operating_cash_flow - capital_expenditures")

ratios = got.get("ratios", {})
computed_net_margin = financials["net_income"] / financials["revenue"]
computed_fcf_margin = financials["free_cash_flow"] / financials["revenue"]
if not math.isclose(ratios.get("net_margin"), computed_net_margin, abs_tol=0.002):
    fail("net_margin is not computed from submitted values")
if not math.isclose(ratios.get("free_cash_flow_margin"), computed_fcf_margin, abs_tol=0.002):
    fail("free_cash_flow_margin is not computed from submitted values")
PY
