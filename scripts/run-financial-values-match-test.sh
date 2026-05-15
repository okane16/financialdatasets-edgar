#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "usage: $0 <variant-id> <answer-json>" >&2
  echo "example: $0 aapl-financialdatasets-rest .axp/sandboxes/<id>/workspace/answer.json" >&2
  exit 2
fi

variant_id="$1"
answer_json="$2"

if [ ! -s "$answer_json" ]; then
  echo "answer JSON not found or empty: $answer_json" >&2
  exit 2
fi

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

mkdir -p "$tmpdir/workspace"
cp "$answer_json" "$tmpdir/workspace/answer.json"

repo_root="$(cd "$(dirname "$0")/.." && pwd)"

docker run --rm \
  -e "AXP_VARIANT_ID=$variant_id" \
  -v "$repo_root/scripts/financial-values-match-test.sh:/test.sh:ro" \
  -v "$tmpdir/workspace:/workspace:ro" \
  axp-base:0.2.0 \
  /bin/bash /test.sh
