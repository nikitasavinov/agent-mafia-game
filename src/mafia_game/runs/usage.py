"""Aggregation helpers for provider usage recorded in run payloads."""

from __future__ import annotations

from collections.abc import MutableMapping

__all__ = ["add_numeric_totals"]


def add_numeric_totals(totals: MutableMapping[str, float], payload: object) -> None:
    """Add numeric entries from a provider usage mapping to ``totals``."""
    if not isinstance(payload, dict):
        return
    for key, value in payload.items():
        number = _optional_number(value)
        if number is not None:
            label = str(key)
            totals[label] = totals.get(label, 0.0) + number


def _optional_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)
