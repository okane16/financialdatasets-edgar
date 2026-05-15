# Financial Datasets vs SEC EDGAR Exploratory Findings Canvas

Last updated: 2026-05-14

## Source Of Truth

Use the latest 20-variant run as the source of truth moving forward:

- Run group: `01KRMD1XXZ76YTXPF0KA3DNY5Q`
- Started: `2026-05-14T23:27:24.096135Z`
- Ended: `2026-05-14T23:33:20.914751Z`
- Parallelism: 4 jobs
- Result: 19 pass / 1 fail / 0 timeout / 0 error
- Token cost recorded by AXP: `$3.036608`

Older full runs are useful for history and debugging, but they predate the current test semantics and should not be used as the primary readout.

## Working Narrative

This eval started as a simple source comparison: can an agent produce normalized FY2023 financials from Financial Datasets REST as reliably as it can from direct SEC EDGAR data?

The stronger story is that the eval exposed several hidden product and benchmark design issues:

- Financial Datasets can be faster for the agent path, but traced API calls make it more expensive than token-only summaries imply.
- Fiscal-year semantics are the main reliability trap, especially for companies whose fiscal year does not align with the calendar year.
- Source separation needs trace-level enforcement. Answer citations alone are not enough to prove the agent used the intended source.
- SEC fact selection needs careful filters. Without `fy` and `fp` filters, the validator can select alternate/amended facts and create false failures.

## Run Inventory

| Run | Date | Shape | Result | Notes |
| --- | --- | ---: | --- | --- |
| `financialdatasets-vs-edgar-normalized-financials-01KR4DGKSAHED75R7P8NTNPJB9` | 2026-05-08 | 20 variants | 17 pass / 3 fail | Exported full run. Failures: `meta-financialdatasets-rest`, `meta-sec-edgar`, `wmt-financialdatasets-rest`. |
| `01KRCQVH9K6Y0T6TQZFV8ZWKZZ` | 2026-05-12 | 20 variants, 1 job | 17 pass / 3 fail | Same failure cluster as the exported run. |
| `01KRCV9863KJTMNDCV1NKFP0DY` | 2026-05-12 | 20 variants, 4 jobs | 17 pass / 3 fail | Parallel local rerun. Same failure cluster. |
| Focused WMT reruns | 2026-05-12 | 5 single variants | Mixed | Used to isolate fiscal-year/source behavior. |
| Focused META reruns | 2026-05-12 | 3 single variants | 2 pass / 1 fail | Used to isolate capex fact-selection behavior. |
| Focused AAPL reruns | 2026-05-12 | 4 single variants | 4 pass | Baseline check after tightening scenario/tests. |
| `01KRD31CGK6BS2NQT744VFKPPQ` | 2026-05-12 | 20 variants, 4 jobs | 19 pass / 1 fail | Latest full local rerun. Only `wmt-financialdatasets-rest` fails, now because it uses SEC EDGAR in a Financial Datasets variant. |
| `01KRMBPSRGX70HJBYXCMEY9M1R` | 2026-05-14 | 20 variants, 4 jobs | 18 pass / 2 fail | Prior post-change run. Failures: `cost-financialdatasets-rest`, `wmt-financialdatasets-rest`. |
| `01KRMD1XXZ76YTXPF0KA3DNY5Q` | 2026-05-14 | 20 variants, 4 jobs | 19 pass / 1 fail | Current source-of-truth run. Failure: `wmt-financialdatasets-rest`. |

There is also an empty local group, `01KRCQPTRBAHHXH545VWPNZYC6`, with no variant run artifacts.

## Exported Full Run: Pairwise Results

Validator output for the exported full run:

- Pairs: 10
- Matching pairs: 9
- Financial Datasets total duration: 576 seconds
- SEC EDGAR total duration: 636 seconds
- Financial Datasets total cost, including traced API calls: `$3.43200125`
- SEC EDGAR total cost: `$1.43739015`

Interpretation:

- Financial Datasets was about 60 seconds faster in aggregate.
- Financial Datasets was about 2.4x the total cost once traced API calls were included.
- The only pairwise value mismatch was WMT.

## Failure Clusters

### Current Source-Of-Truth Failures

In run `01KRMD1XXZ76YTXPF0KA3DNY5Q`, the failing variant is:

- `wmt-financialdatasets-rest`: returned `report_period` `2024-01-31` from Financial Datasets instead of expected FY2023 period `2023-01-31`, so it failed `financial-values-match`.

### WMT: Fiscal-Year Semantics and Data Availability

WMT is the clearest product/eval finding.

In the exported full run, `wmt-financialdatasets-rest` returned report period `2024-01-31` for FY2023, while the SEC-backed expected value was `2023-01-31`. That caused mismatches across revenue, net income, assets, operating cash flow, capex, free cash flow, and ratios.

In one post-change full local rerun, the agent produced the right WMT FY2023 values, but it did so by using SEC EDGAR and labeling the answer `sec-edgar` inside a `financialdatasets-rest` variant. The tightened tests caught both problems:

- `source-kind-match`: `source_kind mismatch: sec-edgar != financialdatasets-rest`
- `source-requests-match-variant`: `Financial Datasets variant used SEC EDGAR`

The answer note says the Financial Datasets plan/API did not include WMT's FY2023 filing because the filing was before the visible coverage cutoff. That turns WMT from a simple model failure into a source-coverage and fiscal-year-labeling finding.

In the current source-of-truth run, the WMT Financial Datasets variant stayed within the intended source but returned `2024-01-31`, the earliest/closest available Financial Datasets annual period, as a proxy. The validator rejected that because the benchmark expects Walmart FY2023 to end on `2023-01-31`.

### META: Validator Fact Selection

Both META variants failed in the exported full run because `financial-values-match` expected capital expenditures of `27045000000`, while both the Financial Datasets and SEC agents returned `27266000000`.

The later local reruns passed after the scenario validator was tightened to filter SEC facts by `fy` and `fp == "FY"` in addition to report period and form. This looks like an eval-design correction, not a product miss.

### Costing: Source URL Cost Understates Actual API Cost

The validator now prices Financial Datasets API usage from traced commands instead of only `answer.source_urls`.

Examples from the exported full run:

- AAPL listed one source URL but made 3 `/financials` calls, so traced API cost was `$0.30`, not `$0.10`.
- GOOGL also made 3 `/financials` calls.
- TSLA made 2 `/financials` calls.
- WMT made 9 Financial Datasets calls before failing, including 5 `/financials` and 4 `/financials/income-statements` calls, for `$0.66` API cost before token cost.

This is a useful storytelling point: API convenience changes both latency and spend, and agent retry behavior matters.

## Candidate Claims

1. Financial Datasets usually lets the agent reach a passing answer faster, but the real cost story depends on actual request count, not final citations.
2. Direct SEC EDGAR is slower and more token-heavy in many cases, but it can be more complete for edge fiscal periods and source-of-truth validation.
3. Non-calendar fiscal years are the stress test. WMT revealed a mismatch between user intent, API coverage/labeling, and validator expectations.
4. Trace-level source checks are required for trustworthy source comparisons. Without them, the agent can accidentally solve a Financial Datasets task with SEC data.
5. The benchmark improved through the failures: META exposed SEC fact-selection ambiguity; WMT exposed source and period ambiguity; cost tracing exposed hidden API spend.

## Possible Canvas Structure

### 1. Setup

Question: "For FY2023 normalized financials, does Financial Datasets REST reduce the work compared with direct SEC EDGAR?"

Design: 10 tickers, paired source variants, same required output schema, value validation against SEC facts, source validation, and post-run pairwise comparison.

### 2. First Readout

Most variants pass. The failures cluster around META and WMT, not random companies.

Use this as the first visual:

- 20-variant full run: 17 pass / 3 fail
- Latest local rerun: 19 pass / 1 fail

### 3. What Changed

The benchmark got stricter:

- FY2023 prompt clarified fiscal-year labeling.
- Validator now filters SEC facts by fiscal year and fiscal period.
- Source tests renamed and tightened.
- Trace introspection checks whether variants actually used the intended source.
- Post-run validator prices API calls from traces.

### 4. Findings

Cost and time:

- Financial Datasets total duration: 576 seconds
- SEC EDGAR total duration: 636 seconds
- Financial Datasets total cost with API calls: `$3.43`
- SEC EDGAR total cost: `$1.44`

Reliability:

- 9 of 10 pairs matched in the exported run.
- WMT did not match because of fiscal-year period selection.
- Latest WMT Financial Datasets rerun only passes value checks by leaking into SEC EDGAR, which the tightened source checks correctly reject.

### 5. Open Questions

- Is WMT's FY2023 absence a Financial Datasets plan/coverage issue, an endpoint behavior issue, or a ticker-specific filing-date edge case?
- Should the benchmark exclude periods outside Financial Datasets coverage, or keep WMT as a deliberate edge case?
- Should API-cost scoring count every observed request, or dedupe identical endpoint/ticker/period requests?
- Should the final story be product-facing, eval-methodology-facing, or customer-value-facing?

## Data Sources Used

- Local AXP run artifacts in `.axp/runs/`
- Exported run group: `.axp/runs/financialdatasets-vs-edgar-normalized-financials-01KR4DGKSAHED75R7P8NTNPJB9`
- Latest full local run: `.axp/runs/01KRD31CGK6BS2NQT744VFKPPQ`
- Current scenario diff in `examples/financialdatasets-vs-edgar-normalized-financials.yaml`
- Current post-run validator diff in `scripts/validate-financialdatasets-edgar-run.py`
