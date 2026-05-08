# Financial Datasets vs SEC EDGAR Evals

Personal evaluation project for comparing Financial Datasets REST API usage against direct SEC EDGAR usage.

## Contents

- `examples/financialdatasets-vs-edgar-normalized-financials.yaml` — 10-stock paired scenario.
- `examples/financialdatasets-vs-edgar-normalized-financials-smoke-cost-fd.yaml` — single-variant smoke scenario.
- `scripts/validate-financialdatasets-edgar-run.py` — post-run validator for paired Financial Datasets vs EDGAR outputs, cost, and duration.

## Run

The scenarios require:

- `ANTHROPIC_API_KEY`
- `FINANCIAL_DATASETS_API_KEY`

Example:

```sh
op run --env-file=.env -- axp run examples/financialdatasets-vs-edgar-normalized-financials.yaml
```

## Post-Run Validation

Run the pairwise validator against a completed run group:

```sh
python3 scripts/validate-financialdatasets-edgar-run.py .axp/runs/<run-group-id>
```

The validator compares paired `financialdatasets-rest` and `sec-edgar` outputs by ticker, then reports match status, token/API/total cost, and duration deltas.
