"""Window column ordering algorithm."""

from __future__ import annotations


def get_predecessors(identity: str, saved_order: list[str]) -> list[str]:
    """Get all identities that should appear left of the given identity."""
    if identity not in saved_order:
        return []
    idx = saved_order.index(identity)
    return saved_order[:idx]
