# Bug: AXP 0.3.3 sandbox image pull timeout

**Reporter:** Olivia Kane  
**Date:** 2026-06-04  
**AXP:** `0.3.3-rp` (`.axp-version`)  
**Platform:** macOS, Docker Desktop 25.0.2, context `desktop-linux`

## Summary

`axp run` fails before any variant executes with:

```text
Error: sandbox backend error: provider error: provider transport error:
pull `514labs/axp-base:0.3.3-rp`: Timeout error
```

`docker pull 514labs/axp-base:0.3.3-rp` on the same machine succeeds in ~5s ("Image is up to date").

## Reproduction

```bash
cd financialdatasets-edgar
docker pull 514labs/axp-base:0.3.3-rp
axp validate experiments/hello-world.yaml
op run --env-file=.env -- axp run experiments/hello-world.yaml -j 1
```

Minimal experiment: `experiments/hello-world.yaml` (1 variant, no fixture setup).

## Not caused by

- Experiment YAML / embedded fixture setup (failure is at image pull, before sandbox exec).
- Missing local image (`docker pull` OK).
- High concurrency only (reproduces at `-j 1`).

## Related (0.3.2)

Intermittent: `container started but port 8800/tcp has no host binding` — likely separate race on port publish inspect.

## Send debug bundle to platform

After a failing `axp run`, engineering can inspect the bundled run + `experiment.yaml` + host `meta.json`:

```bash
axp auth login   # or: axp auth connect

axp send-debug \
  --note "0.3.3-rp pull timeout: axp run fails, docker pull OK, hello-world -j1, macOS Docker Desktop 25.0.2" \
  --no-workspace \
  --no-fs-diff \
  --out ./axp-debug-hello-world.tgz

# Omit RUN_ID to use most recent run, or pass e.g. 01KT8AX4ZSH61QC52N4HPV2SNS
```

Use `--dry-run --out ./bundle.tgz` to inspect locally without uploading.

**Repo:** https://github.com/okane16/financialdatasets-edgar
