# Niri Window Column Ordering Design

## Overview

Extends the unified niri window tracking system to save and restore window column order within workspaces. Uses a localized placement algorithm that positions each window relative to its predecessors, achieving correct ordering without disruptive full-workspace reordering.

## Problem

When restoring windows:
- Windows may spawn at arbitrary times and in arbitrary order
- Extra windows may have been added since last save
- Focusing windows is required to reorder them (visually disruptive)
- niri-spacer windows must remain leftmost in their workspace

## Goals

1. **Best-effort ordering**: Restore windows to their saved column positions when possible
2. **Minimal disruption**: Only focus/move the newly spawned window, not existing ones
3. **Spacer-aware**: Respect niri-spacer's leftmost position requirement
4. **Graceful degradation**: Handle missing windows, extra windows, and out-of-order spawns

## Algorithm: Localized Placement with Predecessor Tracking

### Core Concept

Each window positions itself relative to its "rightmost present predecessor"—the window that should be immediately to its left that's currently in the workspace.

### Definitions

- **Saved order**: Ordered list of window identities per workspace (e.g., `["tmux:dotfiles", "mosh:server:main", "librewolf:uuid-123"]`)
- **Predecessors of X**: All windows that should appear left of X in saved order
- **Spacer**: Always an implicit predecessor of all tracked windows (if present)

### Placement Algorithm

When window X spawns in workspace W:

```python
def place_window(window_id: int, identity: str, workspace_id: int) -> None:
    saved_order = storage.get_order(workspace_id)
    current_windows = get_workspace_windows(workspace_id)

    # Find predecessors from saved order
    predecessors = get_predecessors(identity, saved_order)

    # Spacer is implicit predecessor of all tracked windows
    spacer = find_spacer_in_workspace(workspace_id)
    if spacer:
        predecessors.append(spacer.identity)

    # Find rightmost present predecessor
    rightmost = None
    rightmost_col = 0
    for pred_identity in predecessors:
        if win := find_window_by_identity(pred_identity, current_windows):
            if win.column > rightmost_col:
                rightmost = win
                rightmost_col = win.column

    # Calculate target column
    if rightmost:
        target_col = rightmost_col + 1
    else:
        target_col = 1  # No predecessors present, go leftmost

    # Move window if needed
    current_col = get_column(window_id)
    if current_col != target_col:
        move_to_column(window_id, target_col)
```

### Why This Works

The algorithm is self-correcting regardless of spawn/processing order:

**Example: Saved order [A, B, C], windows spawn as C, B, A**

```
Spawn: {C:1, B:2, A:3}
Process C: preds [A,B] present, rightmost A@3 → move right of A → {B:1, A:2, C:3}
Process A: no preds → target col 1 → move left → {A:1, B:2, C:3}
Process B: pred A@1 → target col 2 → already there ✓
Final: {A:1, B:2, C:3} ✓
```

**Example: Spacer in anomalous position**

```
Start: {C:1, A:2, spacer:3, B:4}
Process B: preds [A, spacer], rightmost spacer@3 → target 4, already there ✓
Process C: preds [A, B, spacer], rightmost B@4 → move right → {A:1, spacer:2, B:3, C:4}
Process A: preds [spacer]@2 → target 3 → move right → {spacer:1, A:2, B:3, C:4}
Final: <spacer>ABC ✓
```

The spacer naturally migrates to column 1 as windows position themselves right of it.

### Handling Unknown Windows

Windows not in saved order are left in place. As known windows claim their positions, unknowns get pushed rightward:

```
Saved: [A, B], unknown U added by user
Start: {A:1, U:2, B:3}
Process A: no preds → col 1 ✓
Process B: pred A@1 → target 2 → move left → {A:1, B:2, U:3}
Final: unknowns end up rightmost
```

## Storage Schema Extension

Extend existing storage format in `~/.local/state/niri/<app>.json`:

```json
{
  "version": 2,
  "entries": {
    "<identity>": {"workspace": 2, "width": 50}
  },
  "workspace_orders": {
    "2": ["tmux:dotfiles", "mosh:server:main"],
    "3": ["librewolf:uuid-123", "tmux:scratch"]
  }
}
```

**Fields:**
- `workspace_orders`: Map from workspace index to ordered list of identities
- Order is left-to-right (index 0 = leftmost after spacer)

**Note:** Orders span apps. A single `orders.json` file (or unified storage) may be cleaner than per-app files for cross-app ordering.

### Alternative: Unified Order Storage

```
~/.local/state/niri/orders.json
{
  "version": 1,
  "workspaces": {
    "2": ["tmux:dotfiles", "mosh:server:main", "librewolf:uuid-abc"],
    "3": ["tmux:scratch"]
  }
}
```

This approach stores ordering separately from per-app properties, making cross-app ordering natural.

## IPC Layer Extension

Add to `src/wlrenv/niri/ipc.py`:

```python
def get_column(window_id: int) -> int:
    """Get window's current column index (1-based)."""
    # Uses pos_in_scrolling_layout from niri

def move_column_left(window_id: int) -> None:
    """Move focused window's column left."""

def move_column_right(window_id: int) -> None:
    """Move focused window's column right."""

def move_to_column(window_id: int, target_col: int) -> None:
    """Focus window and move to target column."""
    focus_window(window_id)
    current = get_column(window_id)

    while current < target_col:
        move_column_right(window_id)
        current += 1

    while current > target_col:
        move_column_left(window_id)
        current -= 1
```

## Tracking Changes

Extend `track.py` to capture column order:

```python
def track_terminals() -> None:
    windows = niri.get_windows(app_id="Alacritty")
    workspace_windows: dict[int, list[tuple[int, str]]] = {}  # ws -> [(col, identity)]

    for window in windows:
        for child in get_child_processes(window.pid):
            if identity := identify_tmux(child):
                storage.store("tmux", identity, window.id)
                col = niri.get_column(window.id)
                workspace_windows.setdefault(window.workspace, []).append((col, f"tmux:{identity}"))
                break
            # ... similar for mosh

    # Save ordering per workspace
    for workspace, entries in workspace_windows.items():
        entries.sort(key=lambda x: x[0])  # Sort by column
        order = [identity for _, identity in entries]
        storage.save_order(workspace, order)
```

## Restore Changes

Extend restore functions to place windows:

```python
def restore_tmux() -> None:
    sessions = get_detached_tmux_sessions()

    for session in sessions:
        props = storage.lookup("tmux", session)
        proc = spawn_terminal(["tmux", "attach-session", "-t", session])

        if props:
            window_id = niri.wait_for_window(pid=proc.pid)
            niri.configure(window_id, workspace=props["workspace"], width=props["width"])

            # Place in correct column order
            place_window(window_id, f"tmux:{session}", props["workspace"])
```

## Spacer Detection

Identify spacer windows by title pattern:

```python
def find_spacer_in_workspace(workspace_id: int) -> Window | None:
    windows = niri.get_windows()
    for win in windows:
        if win.workspace_id == workspace_id and win.title == "niri-spacer window":
            return win
    return None
```

## Edge Cases

### Window spawn before tracking data loaded
- Placement uses whatever predecessors are currently visible
- Self-corrects as more windows spawn

### Workspace changed since save
- Window may have moved workspaces manually
- Ordering only applies within current workspace; cross-workspace moves are separate

### Multiple windows with same identity
- Shouldn't happen (identity is unique per app)
- If it does, first match wins

### Spacer not yet created
- Falls back to column 1 for windows with no predecessors
- Once spacer appears and windows are re-processed, order corrects

## Implementation Phases

1. **Storage**: Add `workspace_orders` to storage schema
2. **IPC**: Add `get_column`, `move_to_column` functions
3. **Tracking**: Capture column order during track operations
4. **Restore**: Integrate `place_window` into restore flow
5. **Testing**: Verify with various spawn orders and edge cases

## Future Considerations

- **Row ordering**: Algorithm extends naturally—track `(column, row)` tuples, position relative to both dimensions
- **Cross-monitor**: May need per-output ordering if workspaces span monitors
- **Performance**: If many windows, batch processing may reduce focus churn
