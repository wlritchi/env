"""Window column ordering algorithm."""

from __future__ import annotations

from wlrenv.niri.ipc import Window

SPACER_IDENTITY = "__spacer__"


def get_predecessors(identity: str, saved_order: list[str]) -> list[str]:
    """Get all identities that should appear left of the given identity."""
    if identity not in saved_order:
        return []
    idx = saved_order.index(identity)
    return saved_order[:idx]


def find_rightmost_predecessor(
    identity: str,
    saved_order: list[str],
    current_windows: dict[str, Window],
) -> Window | None:
    """Find the rightmost present predecessor of the given identity.

    Spacer is an implicit predecessor of all tracked windows.
    Returns None if no predecessors are present.
    """
    predecessors = get_predecessors(identity, saved_order)

    # Spacer is implicit predecessor of all tracked windows
    if SPACER_IDENTITY not in predecessors:
        predecessors = [SPACER_IDENTITY] + predecessors

    rightmost: Window | None = None
    rightmost_col = 0

    for pred_id in predecessors:
        if pred_id in current_windows:
            window = current_windows[pred_id]
            if window.column is not None and window.column > rightmost_col:
                rightmost = window
                rightmost_col = window.column

    return rightmost


def calculate_target_column(
    identity: str,
    saved_order: list[str],
    current_windows: dict[str, Window],
) -> int:
    """Calculate the target column for a window based on saved order.

    Returns 1 if no predecessors are present, otherwise returns
    the rightmost predecessor's column + 1.
    """
    rightmost = find_rightmost_predecessor(identity, saved_order, current_windows)
    if rightmost is None or rightmost.column is None:
        return 1
    return rightmost.column + 1
