# Financial Datasets vs SEC EDGAR Evals

AXP benchmark comparing **Financial Datasets REST** vs **SEC EDGAR** for each ticker’s **latest reported** fiscal quarter.

## Experiment

- **YAML:** `experiments/financialdatasets-vs-edgar-latest-reported-financials.yaml`
- **Matrix:** 10 tickers × 2 sources = **20 variants**
- **AXP version:** `branch:nico-eng-3280-stage-local-wip-files-into-variant` (see `.axp-version`)

## Prerequisites

- [AXP](https://docs.514.ai) PR 472 dev build (install: `bash <(curl -fsSL https://download.514.ai/install.sh) axp@branch:nico-eng-3280-stage-local-wip-files-into-variant`)
- Docker Desktop (running)
- API keys in `.env` (copy from `.env.example`)

```sh
cp .env.example .env
# Fill ANTHROPIC_API_KEY and FINANCIAL_DATASETS_API_KEY (1Password refs OK with op run)
```

## Run

**Sandbox smoke** (no fixtures, one variant — use this to verify Docker/AXP first):

```sh
docker pull 514labs/axp-base:dev-nico-eng-3280-stage-local-wip-files-into-variant
axp validate experiments/hello-world.yaml
op run --env-file=.env -- axp run experiments/hello-world.yaml -j 1
```

**Full benchmark** (20 variants):

```sh
docker pull 514labs/axp-base:dev-nico-eng-3280-stage-local-wip-files-into-variant   # recommended before first run

op run --env-file=.env -- axp run \
  experiments/financialdatasets-vs-edgar-latest-reported-financials.yaml \
  -n 1 -j 5
```

Artifacts: `.axp/runs/<run-group-id>/` (gitignored).

Upload to platform (layout 2.0):

```sh
axp upload <run-group-id>
```

## Ground truth (fixtures)

| File | Role |
|------|------|
| `fixtures/tickers.json` | Symbol + CIK registry |
| `fixtures/metric-policies.json` | SEC tag/method policies |
| `fixtures/latest-reporting-period-core.json` | Expected period + 6 core metrics per ticker |
| `fixtures/yahoo-cross-reference.json` | Yahoo cross-check (regenerated; may lag SEC) |

**Cutoff:** `as_of_date` in the core fixture (currently **2026-06-03**). Regenerate when new filings land:

```sh
python3 scripts/inspect-latest-sec-financials.py \
  --as-of-date $(date +%Y-%m-%d) \
  --fixture-json fixtures/latest-reporting-period-core.json
python3 scripts/build-yahoo-cross-reference.py
```

## Validate before run

```sh
python3 scripts/validate-core-fixture.py
python3 scripts/validate-experiment-coverage.py
python3 scripts/validate-prompt-schema-alignment.py
python3 scripts/inspect-latest-sec-financials.py --check-drift fixtures/latest-reporting-period-core.json
```

## Debugging notes (AXP 0.3.x + Docker)

- **Port 8800 / no host binding:** intermittent Docker Desktop race; retry the run or lower `-j`.
- **`pull 514labs/axp-base:dev-nico-eng-3280-stage-local-wip-files-into-variant: Timeout`:** pre-pull the image; axp’s internal pull deadline can fail even when the image is local.
- **Sandbox has no repo mount:** fixtures/scripts are staged into `/workspace` via the experiment `files:` entries before setup checks run.
- **Secrets:** use `op run --env-file=.env -- axp run …` when keys are 1Password references.

## Docs

- [`docs/expected-dataset-ground-truth-plan.md`](docs/expected-dataset-ground-truth-plan.md) — how expected values are defined and maintained.
