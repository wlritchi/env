"""Window column ordering algorithm."""

from __future__ import annotations

from wlrenv.niri import ipc, order_storage
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


def move_to_column(window_id: int, current_column: int, target_column: int) -> None:
    """Move a window to the target column.

    Focuses the window and moves it left or right as needed.
    Does nothing if already at target column.
    """
    if current_column == target_column:
        return

    ipc.focus_window(window_id)

    while current_column > target_column:
        ipc.move_column_left()
        current_column -= 1

    while current_column < target_column:
        ipc.move_column_right()
        current_column += 1


def _build_current_windows(
    workspace_id: int,
    exclude_window_id: int | None = None,
) -> dict[str, Window]:
    """Build a mapping of identity -> Window for current windows in workspace.

    Identifies windows by their title (which should contain the identity).
    Spacer windows are mapped to SPACER_IDENTITY.
    """
    windows = ipc.get_windows()
    current: dict[str, Window] = {}

    for w in windows:
        if w.workspace_id != workspace_id:
            continue
        if exclude_window_id is not None and w.id == exclude_window_id:
            continue

        # Check for spacer window
        if w.title == "niri-spacer window":
            current[SPACER_IDENTITY] = w
        else:
            # For now, use title as identity (will be refined in integration)
            # In practice, the caller will provide the correct identity mapping
            current[w.title] = w

    return current


def place_window(
    window_id: int,
    identity: str,
    workspace_id: int,
    current_column: int,
) -> None:
    """Place a window in the correct column based on saved order.

    This is the main entry point for window ordering during restore.
    """
    saved_order = order_storage.get_order(workspace_id)

    # Build current window state (excluding the window we're placing)
    current_windows = _build_current_windows(workspace_id, exclude_window_id=window_id)

    # Calculate target column
    target = calculate_target_column(identity, saved_order, current_windows)

    # Move if needed
    move_to_column(window_id, current_column, target)
