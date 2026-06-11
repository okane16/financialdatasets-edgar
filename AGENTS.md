## Learned User Preferences

- Grade **outputs** (answer.json + state), not tool paths; use traces for provenance after the run.
- Keep provider-comparison prompts neutral except each variant’s required source (FD REST vs SEC EDGAR).
- Reject hardcoded fixture overrides; use `metric-policies.json` for SEC resolution.

## Learned Workspace Facts

- **Active experiment only:** `experiments/financialdatasets-vs-edgar-latest-reported-financials.yaml` (20 variants).
- **Fixture authority:** `fixtures/latest-reporting-period-core.json` at `as_of_date`; regenerate with `inspect-latest-sec-financials.py` when filings advance.
- **Staging:** AXP sandboxes get `fixtures/` and the validator helper scripts via top-level experiment `files:` entries; graders read `/workspace/fixtures` through `eval_bootstrap.py`.
- **AXP pin:** `.axp-version` → `branch:nico-eng-3280-stage-local-wip-files-into-variant` (PR 472 dev build); layout 2.0 for `axp upload`.
- **Run:** `op run --env-file=.env -- axp run experiments/...yaml -n 1 -j 5`
- **Known flakes:** Docker port-8800 binding; axp-base image pull timeout under high `-j` — pre-pull `514labs/axp-base:dev-nico-eng-3280-stage-local-wip-files-into-variant`.
