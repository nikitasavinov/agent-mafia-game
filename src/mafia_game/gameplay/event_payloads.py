"""Defensive readers for values stored in ``GameEvent.data``.

Event payloads are decoded from JSON values stored in SQLite. Readers treat
every value as untrusted instead of assuming persisted data has the expected
runtime type.
"""

from __future__ import annotations

__all__ = ["optional_int"]


def optional_int(value: object) -> int | None:
    """Read ``value`` as an integer, rejecting booleans and invalid strings."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None
