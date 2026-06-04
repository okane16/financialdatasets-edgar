#!/usr/bin/env python3
"""Load the canonical ticker registry used by fixtures, generators, and eval validators."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fixture_paths import resolve_tickers_path

TickerEntry = dict[str, str]


def load_ticker_registry() -> dict[str, TickerEntry]:
    path = resolve_tickers_path()
    data = json.loads(path.read_text(encoding="utf-8"))
    tickers = data.get("tickers")
    if not isinstance(tickers, dict) or not tickers:
        raise ValueError(f"invalid ticker registry: {path}")
    for key, entry in tickers.items():
        if not isinstance(entry, dict):
            raise ValueError(f"invalid ticker entry for {key}")
        if "symbol" not in entry or "cik" not in entry:
            raise ValueError(f"ticker {key} missing symbol or cik")
    return tickers


def ticker_keys() -> list[str]:
    return sorted(load_ticker_registry())


def symbol_for_key(ticker_key: str) -> str:
    registry = load_ticker_registry()
    key = ticker_key.lower()
    if key not in registry:
        raise KeyError(f"unknown ticker key: {ticker_key}")
    return registry[key]["symbol"]


def cik_for_key(ticker_key: str) -> str:
    registry = load_ticker_registry()
    key = ticker_key.lower()
    if key not in registry:
        raise KeyError(f"unknown ticker key: {ticker_key}")
    return registry[key]["cik"]


def metadata_dict() -> dict[str, dict[str, str]]:
    """Generator-compatible map: lowercase key -> {ticker, cik}."""
    return {
        key: {"ticker": entry["symbol"], "cik": entry["cik"]}
        for key, entry in load_ticker_registry().items()
    }


def validate_ticker_keys(ticker_keys_to_check: list[str]) -> list[str]:
    registry = set(load_ticker_registry())
    errors: list[str] = []
    for key in ticker_keys_to_check:
        if key.lower() not in registry:
            errors.append(f"unknown ticker key: {key}")
    return errors
