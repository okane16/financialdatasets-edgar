# Expected dataset ground truth plan

**Status:** Active for dataset definition and cross-reference workflow. **Implementation phases below are tabled** until the Yahoo-aligned expected dataset is complete and signed off.

**Last updated:** 2026-06-02

---

## Purpose

Define how we build and maintain **expected values** for the `financialdatasets-edgar` eval suite so agents are graded against **human statement semantics** that match what users see on quote-site financials—not against an accidental SEC `companyfacts` aggregate when those differ.

This document:

1. Locks the **expected dataset authority** (Yahoo Finance financials).
2. Describes how to **cross-reference** every graded metric for every ticker in the current fixture.
3. Records the **tabled** technical plan for automating SEC/XBRL resolution later.

---

## Expected dataset authority

### Source of truth (external)

**Primary reference:** [Yahoo Finance](https://finance.yahoo.com) → symbol → **Financials** tab → **Quarterly** (or **Annual** only when the eval period is fiscal year-end).

For each ticker in `fixtures/tickers.json`, use the row that corresponds to the **most recent report period already filed** as of the fixture’s `as_of_date` (`fixtures/latest-reporting-period-core.json` → `as_of_date`, per-ticker `period.report_period`).

That Yahoo row is the **expected dataset** for human-facing semantics. MarketWatch and similar sites usually align with the same operating-revenue line; **Yahoo is the canonical cross-reference for this project**.

### Source of truth (in-repo)

**Committed expected values** live in:

| File | Role |
|------|------|
| `fixtures/latest-reporting-period-core.json` | Per-ticker `period` + `core_values` used by YAML graders |
| `fixtures/metric-policies.json` | How we *derive* SEC-side candidates today (tag ladder, `filing_dimensional` for XOM) |
| `fixtures/tickers.json` | Symbol + CIK registry |

**Rule:** `latest-reporting-period-core.json` must **agree with Yahoo** for every graded field at the matched period. The generator (`scripts/inspect-latest-sec-financials.py`) is a **drafting aid**, not an override of Yahoo when they disagree.

### Semantic definition: revenue

**`income_statement.revenue`** = the main **operating sales / total revenue** line on the consolidated quarterly income statement as Yahoo presents it (not “total revenues and other income” when Yahoo shows operating sales separately—e.g. XOM).

Other core fields use the same principle: match the **label and period** a user sees on Yahoo Financials for that quarter.

---

## Graded metrics (full cross-reference matrix)

Cross-check **every** `core_values` field below for **all 10 tickers**. Record Yahoo value, fixture value, delta, and pass/fail within grading tolerance.

| Section | Field | Yahoo Financials mapping (typical) | Notes |
|---------|--------|-----------------------------------|--------|
| `income_statement` | `revenue` | Total Revenue / Revenue | Watch integrated oils (XOM): operating sales vs total incl. affiliates |
| `income_statement` | `net_income` | Net Income Common Stockholders / Net Income | Align to consolidated net income for the quarter |
| `balance_sheet` | `total_assets` | Total Assets | Point-in-time at quarter-end (balance sheet date = `report_period`) |
| `cash_flow` | `operating_cash_flow` | Operating Cash Flow | Quarterly flow, not YTD unless Yahoo only shows YTD |
| `cash_flow` | `capital_expenditures` | Capital Expenditure | Usually positive magnitude; fixture stores outflow as positive spend |
| `cash_flow` | `free_cash_flow` | Free Cash Flow (if shown) or **OCF − CapEx** | Fixture must satisfy `free_cash_flow = operating_cash_flow - capital_expenditures` |

**Period alignment:** Yahoo quarter end date must match `expected_by_ticker[ticker].period.report_period` (e.g. WMT `2026-04-30`, AAPL `2026-03-28`). If Yahoo has not updated to the latest filed quarter, note lag and do not change the fixture period without updating the whole scenario.

**Grading tolerance** (from fixture `grading`): `relative_tolerance: 0.002`, `absolute_tolerance_usd: 5_000_000`. Differences within tolerance count as match.

---

## Cross-reference workflow (current phase)

Implementation automation is **tabled**. Until then, maintain a human-reviewed cross-reference artifact.

### Step 1 — Anchor period per ticker

From `fixtures/latest-reporting-period-core.json`, for each ticker record:

- `symbol` (from `fixtures/tickers.json`)
- `report_period` (quarter end)
- `fiscal_year` / `fiscal_period`
- `as_of_date` (SEC facts cutoff)

### Step 2 — Pull Yahoo Financials

For each symbol:

1. Open Yahoo Finance → **Financials** → **Quarterly** income statement, balance sheet, cash flow.
2. Select the column for **`report_period`** (most recent filed quarter in the fixture).
3. Record values in USD (Yahoo often displays in millions—convert to full dollars for fixture comparison).

### Step 3 — Compare to fixture

For each metric in the matrix above:

```
delta_usd = fixture_value - yahoo_value
match = abs(delta_usd) <= max(5_000_000, 0.002 * abs(yahoo_value))
```

### Step 4 — Resolve mismatches

| Situation | Action |
|-----------|--------|
| Fixture ≠ Yahoo, outside tolerance | **Update fixture** to Yahoo after verifying period and line item (not generator default) |
| Fixture = Yahoo | Mark verified; no change |
| Yahoo lags filing (column missing) | Document in cross-reference; keep fixture period; re-check when Yahoo updates |
| Ambiguous Yahoo label (e.g. multiple revenue lines) | Use income-statement **first major operating revenue** row; document choice in cross-reference notes |
| SEC `companyfacts` ≠ Yahoo but fixture = Yahoo | **Correct for eval**; note in ticker notes for future `statement_semantic` work |

### Step 5 — Update committed expected file

After review, edit `fixtures/latest-reporting-period-core.json` only where Yahoo-verified values differ. Regenerate is optional if hand-editing; if using generator, **override policy** must not contradict Yahoo-signed values.

Run validation:

```sh
python3 scripts/validate-core-fixture.py
python3 scripts/inspect-latest-sec-financials.py --check-drift fixtures/latest-reporting-period-core.json
python3 scripts/validate-experiment-coverage.py
```

### Cross-reference artifact

Committed file: `fixtures/yahoo-cross-reference.json` (regenerate with `python3 scripts/build-yahoo-cross-reference.py`).

Uses Yahoo `fundamentals-timeseries` quarterly API. When SEC `report_period` does not exactly match Yahoo’s column date, the script picks the closest Yahoo `asOfDate` within **14 days** and records `yahoo_as_of_date` plus a note.

Schema (per metric):

```json
{
  "as_of_date": "2026-05-29",
  "yahoo_checked_at": "2026-06-02",
  "tickers": {
    "xom": {
      "report_period": "2026-03-31",
      "yahoo_url": "https://finance.yahoo.com/quote/XOM/financials/",
      "metrics": {
        "income_statement.revenue": { "yahoo": 83161000000, "fixture": 83161000000, "match": true },
        "income_statement.net_income": { "yahoo": null, "fixture": 4183000000, "match": null, "notes": "fill from Yahoo" }
      }
    }
  }
}
```

Complete all fields for all tickers before closing this phase. **Do not** add generator/policy code changes as part of this phase unless a fixture correction requires a one-line policy fix (prefer hand-fix in core JSON first).

---

## Current ticker registry (cross-reference scope)

| Key | Symbol | Fixture `report_period` (verify in core JSON) |
|-----|--------|-----------------------------------------------|
| `aapl` | AAPL | See `latest-reporting-period-core.json` |
| `cost` | COST | |
| `googl` | GOOGL | |
| `jnj` | JNJ | |
| `meta` | META | |
| `msft` | MSFT | |
| `pg` | PG | |
| `tsla` | TSLA | |
| `wmt` | WMT | |
| `xom` | XOM | |

---

## Known structural issues (inform cross-reference, not blockers)

These explain why SEC automation may disagree with Yahoo until the tabled work ships:

| Pattern | Example | Yahoo vs `companyfacts` |
|---------|---------|-------------------------|
| **A — Dedicated revenue tag** | Most tech/retail | Usually aligned |
| **B — Single `Revenues` line** | Many filers | Aligned |
| **C — Stacked lines, one tag + segments** | XOM | Yahoo ≈ operating sales; `companyfacts` ≈ total incl. affiliates |
| **D — Total + components** | Some oils | May need formula from filing |
| **E — Missing/stale facts** | Edge cases | Manual review |

**XOM (validated story):** Yahoo revenue ≈ **$83.161B** (sales and other operating revenue). SEC `companyfacts` `Revenues` ≈ **$85.138B** (total revenues and other income). Fixture should match **Yahoo**; today’s policy uses `filing_dimensional` to reproduce that line from inline XBRL.

---

## Tabled: automated statement-semantic resolver

The following phases are **deferred** until the Yahoo cross-reference dataset is complete and reviewers agree fixture = Yahoo for all metrics.

### Phase 0 — Contract

- Add `revenue_semantics` and `filing_fallback` config to `metric-policies.json`.
- Document strategies A–E in README.

### Phase 1 — Probe & classify

- New `scripts/probe-revenue-resolution.py` → `fixtures/revenue-resolution-probe.json`.
- Per ticker: `strategy`, `cf_value`, `filing_operating_candidate`, `delta_pct`, `recommended_method`.

### Phase 2 — `statement_semantic` resolver

- Refactor `inspect-latest-sec-financials.py`: companyfacts first → gap detection → filing fallback.
- Extract filing parse module; cache by `(cik, accn)`.
- Auto-detect class C where possible; reduce per-ticker overrides.

### Phase 3 — Segment registry

- `segment_pattern_registry` in policy; overrides only for heuristic failures.

### Phase 4 — Formula path (class D)

- `total Revenues − equity method − other` when components exist in filing.

### Phase 5 — CI

- Probe in CI; fail on `strategy: review` or unverified Yahoo drift.
- Quarterly regen alerts when strategy flips.

### Phase 6 — Eval copy

- Grader hints: expected semantics = Yahoo-style operating lines.
- Optional analytics column for resolution strategy.

### Open decisions (when un-tabled)

1. `max_cf_vs_filing_gap` tolerance (0.5% vs 2%).
2. Always fetch filing for probe vs only on mismatch.
3. Generator fail vs `review` flag when class C and parse fails.
4. Financial Datasets API returning `companyfacts`-style total for XOM while SEC eval grades Yahoo line—document provider skew vs normalize FD separately.

---

## Success criteria (dataset phase — active now)

1. **Coverage:** All 10 tickers × 6 core metrics cross-referenced against Yahoo at the fixture `report_period`.
2. **Authority:** Every value in `latest-reporting-period-core.json` matches Yahoo within grading tolerance, or has a documented exception with reviewer sign-off.
3. **Period integrity:** `report_period` matches the Yahoo column used; `as_of_date` still filters SEC filings correctly.
4. **Identity:** `free_cash_flow = operating_cash_flow - capital_expenditures` holds for all tickers.
5. **Transparency:** Cross-reference artifact (or equivalent spreadsheet) records Yahoo URLs, check date, and notes for ambiguous lines.

## Success criteria (automation phase — tabled)

1. New tickers: probe → classify → minimal registry entry → regen without `fixed_value`.
2. No filing fetch when Yahoo and `companyfacts` already agree (class A/B).
3. XOM-like filers auto-resolve to operating sales line.

---

## Related files

- `fixtures/latest-reporting-period-core.json` — expected values (must match Yahoo)
- `fixtures/metric-policies.json` — SEC derivation policy (tabled expansion)
- `scripts/inspect-latest-sec-financials.py` — generator
- `scripts/validate-core-fixture.py` — structural validation
- `experiments/financialdatasets-vs-edgar-latest-reported-financials.yaml` — graders consuming fixture
- `AGENTS.md` (workspace) — preference: MarketWatch-style fields; **this doc supersedes for Yahoo as canonical cross-reference**

---

## Revision log

| Date | Change |
|------|--------|
| 2026-06-02 | Initial plan: Yahoo as expected authority; cross-reference workflow active; resolver phases tabled |
