"""Window column ordering algorithm."""

from __future__ import annotations

from wlrenv.niri import ipc, positions
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
    """Place a window in the correct column based on predecessor positions.

    This is the main entry point for window ordering during restore.
    Uses positions.find_predecessors() for cross-app ordering from historical boots.
    """
    # Parse app from identity (e.g., "tmux:session" -> "tmux")
    app = identity.split(":")[0] if ":" in identity else identity

    # Find predecessors from historical boots
    predecessor_ids = positions.find_predecessors(
        stable_id=identity,
        this_app=app,
        workspace_id=workspace_id,
    )

    # Resolve to window IDs in current boot
    predecessor_window_ids = positions.resolve_predecessors_to_window_ids(
        predecessor_ids=predecessor_ids,
        workspace_id=workspace_id,
    )

    # Get all windows in workspace to find predecessor columns
    windows = ipc.get_windows()
    window_id_to_column: dict[int, int] = {}
    spacer_column: int | None = None

    for w in windows:
        if w.workspace_id != workspace_id:
            continue
        if w.column is not None:
            window_id_to_column[w.id] = w.column
            if w.title == "niri-spacer window":
                spacer_column = w.column

    # Find rightmost predecessor column
    rightmost_col = 0

    # Spacer is implicit predecessor
    if spacer_column is not None and spacer_column > rightmost_col:
        rightmost_col = spacer_column

    for pred_window_id in predecessor_window_ids:
        col = window_id_to_column.get(pred_window_id)
        if col is not None and col > rightmost_col:
            rightmost_col = col

    # Target is one right of rightmost predecessor (or column 1 if none)
    target = rightmost_col + 1 if rightmost_col > 0 else 1

    # Move if needed
    move_to_column(window_id, current_column, target)
