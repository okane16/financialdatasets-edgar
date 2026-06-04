#!/usr/bin/env python3
"""Shared helpers for inline Python blocks in experiment YAML validators."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from fixture_paths import resolve_core_fixture_path
from ticker_registry import symbol_for_key


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def bootstrap_scripts() -> None:
    search_roots = [
        Path("/workspace"),
        Path("/opt/axp-eval"),
        Path.cwd(),
        *Path.cwd().parents,
    ]
    for parent in search_roots:
        scripts = parent / "scripts"
        if (scripts / "ticker_registry.py").is_file():
            if str(scripts) not in sys.path:
                sys.path.insert(0, str(scripts))
            return
    fail("scripts/ticker_registry.py not found")


def load_core_fixture() -> dict:
    try:
        path = resolve_core_fixture_path()
    except FileNotFoundError as exc:
        fail(str(exc))
    fixture = json.loads(path.read_text(encoding="utf-8"))
    if "expected_by_ticker" not in fixture:
        fail(f"invalid core fixture: {path}")
    if not fixture.get("as_of_date") and not fixture.get("metadata", {}).get("as_of_date"):
        fail(f"core fixture missing as_of_date: {path}")
    return fixture


def expected_symbol(ticker_key: str) -> str:
    try:
        return symbol_for_key(ticker_key)
    except KeyError as exc:
        fail(str(exc))
