#!/usr/bin/env python3
"""Resolve fixture file paths for eval validators and generators."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def fixture_candidates(filename: str) -> list[Path]:
    return [
        Path("/workspace/fixtures") / filename,
        Path("/opt/axp-eval/fixtures") / filename,
        REPO_ROOT / "fixtures" / filename,
        Path("fixtures") / filename,
    ]


def resolve_fixture_path(filename: str) -> Path:
    for candidate in fixture_candidates(filename):
        if candidate.is_file():
            return candidate
    searched = ", ".join(str(path) for path in fixture_candidates(filename))
    raise FileNotFoundError(f"fixture not found ({filename}); searched: {searched}")


def resolve_core_fixture_path() -> Path:
    return resolve_fixture_path("latest-reporting-period-core.json")


def resolve_tickers_path() -> Path:
    return resolve_fixture_path("tickers.json")
