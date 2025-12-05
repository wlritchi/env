# Niri Window Column Ordering Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add column ordering to niri window tracking so windows restore to their saved column positions.

**Architecture:** Extend existing niri module with column position tracking in IPC layer, a new `ordering.py` module for placement algorithm, unified order storage in `orders.json`, and integration into track/restore flows.

**Tech Stack:** Python 3.12+, pytest, existing wlrenv.niri module, niri IPC via `niri msg`

---

## Task 1: Extend Window Dataclass with Column Position

**Files:**
- Modify: `src/wlrenv/niri/ipc.py:17-28`
- Test: `tests/niri/test_ipc.py`

**Step 1: Write the failing test**

Add to `tests/niri/test_ipc.py`:

```python
@patch("wlrenv.niri.ipc._run_niri_msg")
def test_get_windows_parses_column_position(mock_run: MagicMock) -> None:
    mock_run.return_value = [make_window_json(id=1)]

    windows = get_windows()

    assert windows[0].column == 1
    assert windows[0].row == 1


@patch("wlrenv.niri.ipc._run_niri_msg")
def test_get_windows_handles_floating_window(mock_run: MagicMock) -> None:
    window_json = make_window_json(id=1)
    window_json["layout"]["pos_in_scrolling_layout"] = None
    window_json["is_floating"] = True
    mock_run.return_value = [window_json]

    windows = get_windows()

    assert windows[0].column is None
    assert windows[0].row is None
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/niri/test_ipc.py::test_get_windows_parses_column_position -v`
Expected: FAIL with `AttributeError: 'Window' object has no attribute 'column'`

**Step 3: Write minimal implementation**

Modify `src/wlrenv/niri/ipc.py` - update Window dataclass and get_windows:

```python
@dataclass
class Window:
    """A niri window."""

    id: int
    title: str
    app_id: str
    pid: int
    workspace_id: int
    tile_width: float
    tile_height: float
    column: int | None = None  # 1-based column index, None for floating
    row: int | None = None     # 1-based row index within column, None for floating
```

Update `get_windows()` to parse position:

```python
def get_windows(app_id: str | None = None) -> list[Window]:
    """Get all windows, optionally filtered by app_id."""
    data = _run_niri_msg(["windows"])

    windows = []
    for w in data:
        if app_id and w.get("app_id") != app_id:
            continue

        # Parse column/row from layout
        column = None
        row = None
        layout = w.get("layout", {})
        pos = layout.get("pos_in_scrolling_layout")
        if pos is not None:
            column, row = pos[0], pos[1]

        windows.append(
            Window(
                id=w["id"],
                title=w.get("title", ""),
                app_id=w.get("app_id", ""),
                pid=w["pid"],
                workspace_id=w["workspace_id"],
                tile_width=layout.get("tile_size", [0, 0])[0],
                tile_height=layout.get("tile_size", [0, 0])[1],
                column=column,
                row=row,
            )
        )
    return windows
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/niri/test_ipc.py -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add src/wlrenv/niri/ipc.py tests/niri/test_ipc.py
git commit -m "feat(niri): add column/row position to Window dataclass"
```

---

## Task 2: Add IPC Functions for Window Movement

**Files:**
- Modify: `src/wlrenv/niri/ipc.py`
- Test: `tests/niri/test_ipc.py`

**Step 1: Write the failing tests**

Add to `tests/niri/test_ipc.py`:

```python
from wlrenv.niri.ipc import (
    configure,
    focus_window,
    get_outputs,
    get_windows,
    move_column_left,
    move_column_right,
)


@patch("wlrenv.niri.ipc._run_niri_msg")
def test_focus_window_calls_correct_action(mock_run: MagicMock) -> None:
    mock_run.return_value = None

    focus_window(42)

    mock_run.assert_called_once()
    args = mock_run.call_args[0][0]
    assert "action" in args
    assert "focus-window" in args
    assert "--id" in args
    assert "42" in args


@patch("wlrenv.niri.ipc._run_niri_msg")
def test_move_column_left_calls_correct_action(mock_run: MagicMock) -> None:
    mock_run.return_value = None

    move_column_left()

    mock_run.assert_called_once()
    args = mock_run.call_args[0][0]
    assert "action" in args
    assert "move-column-left" in args


@patch("wlrenv.niri.ipc._run_niri_msg")
def test_move_column_right_calls_correct_action(mock_run: MagicMock) -> None:
    mock_run.return_value = None

    move_column_right()

    mock_run.assert_called_once()
    args = mock_run.call_args[0][0]
    assert "action" in args
    assert "move-column-right" in args
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/niri/test_ipc.py::test_focus_window_calls_correct_action -v`
Expected: FAIL with `ImportError: cannot import name 'focus_window'`

**Step 3: Write minimal implementation**

Add to `src/wlrenv/niri/ipc.py`:

```python
def focus_window(window_id: int) -> None:
    """Focus a window by ID."""
    _run_niri_msg(
        ["action", "focus-window", "--id", str(window_id)],
        json_output=False,
    )


def move_column_left() -> None:
    """Move the focused window's column left."""
    _run_niri_msg(["action", "move-column-left"], json_output=False)


def move_column_right() -> None:
    """Move the focused window's column right."""
    _run_niri_msg(["action", "move-column-right"], json_output=False)
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/niri/test_ipc.py -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add src/wlrenv/niri/ipc.py tests/niri/test_ipc.py
git commit -m "feat(niri): add focus_window and move_column_left/right IPC functions"
```

---

## Task 3: Create Order Storage Module

**Files:**
- Create: `src/wlrenv/niri/order_storage.py`
- Test: `tests/niri/test_order_storage.py`

**Step 1: Write the failing tests**

Create `tests/niri/test_order_storage.py`:

```python
# tests/niri/test_order_storage.py
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def temp_state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from wlrenv.niri import config

    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    return tmp_path


def test_get_order_returns_empty_list_for_missing_workspace(
    temp_state_dir: Path,
) -> None:
    from wlrenv.niri.order_storage import get_order

    order = get_order(workspace_id=1)

    assert order == []


def test_save_order_persists_data(temp_state_dir: Path) -> None:
    from wlrenv.niri.order_storage import get_order, save_order

    save_order(workspace_id=2, order=["tmux:dotfiles", "mosh:server:main"])

    order = get_order(workspace_id=2)
    assert order == ["tmux:dotfiles", "mosh:server:main"]


def test_save_order_overwrites_existing(temp_state_dir: Path) -> None:
    from wlrenv.niri.order_storage import get_order, save_order

    save_order(workspace_id=2, order=["a", "b"])
    save_order(workspace_id=2, order=["c", "d", "e"])

    order = get_order(workspace_id=2)
    assert order == ["c", "d", "e"]


def test_orders_are_per_workspace(temp_state_dir: Path) -> None:
    from wlrenv.niri.order_storage import get_order, save_order

    save_order(workspace_id=1, order=["a"])
    save_order(workspace_id=2, order=["b", "c"])

    assert get_order(workspace_id=1) == ["a"]
    assert get_order(workspace_id=2) == ["b", "c"]
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/niri/test_order_storage.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'wlrenv.niri.order_storage'`

**Step 3: Write minimal implementation**

Create `src/wlrenv/niri/order_storage.py`:

```python
"""Storage for workspace column ordering."""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any

from wlrenv.niri.config import STATE_DIR


def _get_orders_path() -> str:
    return str(STATE_DIR / "orders.json")


def _load() -> dict[str, Any]:
    """Load orders data, returning empty structure if missing."""
    path = _get_orders_path()
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)  # type: ignore[no-any-return]
    return {"version": 1, "workspaces": {}}


def _save(data: dict[str, Any]) -> None:
    """Atomically save orders data."""
    path = _get_orders_path()
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    # Write to temp file, then atomic rename
    fd, tmp_path = tempfile.mkstemp(dir=STATE_DIR, suffix=".json.tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.rename(tmp_path, path)
    except BaseException:
        os.unlink(tmp_path)
        raise


def get_order(workspace_id: int) -> list[str]:
    """Get ordered list of window identities for workspace."""
    data = _load()
    return data["workspaces"].get(str(workspace_id), [])


def save_order(workspace_id: int, order: list[str]) -> None:
    """Save ordered list of window identities for workspace."""
    data = _load()
    data["workspaces"][str(workspace_id)] = order
    _save(data)
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/niri/test_order_storage.py -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add src/wlrenv/niri/order_storage.py tests/niri/test_order_storage.py
git commit -m "feat(niri): add order_storage module for workspace column ordering"
```

---

## Task 4: Create Ordering Module with Placement Algorithm

**Files:**
- Create: `src/wlrenv/niri/ordering.py`
- Test: `tests/niri/test_ordering.py`

**Step 1: Write the failing tests for get_predecessors**

Create `tests/niri/test_ordering.py`:

```python
# tests/niri/test_ordering.py
from __future__ import annotations


def test_get_predecessors_returns_empty_for_first_in_order() -> None:
    from wlrenv.niri.ordering import get_predecessors

    preds = get_predecessors("tmux:a", ["tmux:a", "tmux:b", "tmux:c"])

    assert preds == []


def test_get_predecessors_returns_all_before_identity() -> None:
    from wlrenv.niri.ordering import get_predecessors

    preds = get_predecessors("tmux:c", ["tmux:a", "tmux:b", "tmux:c"])

    assert preds == ["tmux:a", "tmux:b"]


def test_get_predecessors_returns_empty_for_unknown_identity() -> None:
    from wlrenv.niri.ordering import get_predecessors

    preds = get_predecessors("tmux:unknown", ["tmux:a", "tmux:b"])

    assert preds == []
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/niri/test_ordering.py::test_get_predecessors_returns_empty_for_first_in_order -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'wlrenv.niri.ordering'`

**Step 3: Write minimal implementation**

Create `src/wlrenv/niri/ordering.py`:

```python
"""Window column ordering algorithm."""

from __future__ import annotations


def get_predecessors(identity: str, saved_order: list[str]) -> list[str]:
    """Get all identities that should appear left of the given identity."""
    if identity not in saved_order:
        return []
    idx = saved_order.index(identity)
    return saved_order[:idx]
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/niri/test_ordering.py -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add src/wlrenv/niri/ordering.py tests/niri/test_ordering.py
git commit -m "feat(niri): add get_predecessors function for ordering"
```

---

## Task 5: Add find_rightmost_predecessor Function

**Files:**
- Modify: `src/wlrenv/niri/ordering.py`
- Modify: `tests/niri/test_ordering.py`

**Step 1: Write the failing tests**

Add to `tests/niri/test_ordering.py`:

```python
from wlrenv.niri.ipc import Window


def make_test_window(
    id: int,
    identity: str,
    column: int,
    workspace_id: int = 1,
) -> tuple[Window, str]:
    """Create a test window and its identity."""
    return (
        Window(
            id=id,
            title=identity,  # Use identity as title for simplicity
            app_id="Alacritty",
            pid=1000 + id,
            workspace_id=workspace_id,
            tile_width=1000.0,
            tile_height=1000.0,
            column=column,
            row=1,
        ),
        identity,
    )


def test_find_rightmost_predecessor_returns_none_when_no_preds_present() -> None:
    from wlrenv.niri.ordering import find_rightmost_predecessor

    # Window A has no predecessors in saved order
    saved_order = ["tmux:a", "tmux:b", "tmux:c"]
    current_windows: dict[str, Window] = {}  # No windows present

    result = find_rightmost_predecessor("tmux:a", saved_order, current_windows)

    assert result is None


def test_find_rightmost_predecessor_finds_rightmost() -> None:
    from wlrenv.niri.ordering import find_rightmost_predecessor

    saved_order = ["tmux:a", "tmux:b", "tmux:c"]
    win_a, _ = make_test_window(1, "tmux:a", column=1)
    win_b, _ = make_test_window(2, "tmux:b", column=3)
    current_windows = {"tmux:a": win_a, "tmux:b": win_b}

    # C's predecessors are A and B; B is rightmost at column 3
    result = find_rightmost_predecessor("tmux:c", saved_order, current_windows)

    assert result == win_b


def test_find_rightmost_predecessor_includes_spacer() -> None:
    from wlrenv.niri.ordering import SPACER_IDENTITY, find_rightmost_predecessor

    saved_order = ["tmux:a", "tmux:b"]
    spacer, _ = make_test_window(99, SPACER_IDENTITY, column=1)
    current_windows = {SPACER_IDENTITY: spacer}

    # A has no saved predecessors, but spacer is implicit predecessor
    result = find_rightmost_predecessor("tmux:a", saved_order, current_windows)

    assert result == spacer
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/niri/test_ordering.py::test_find_rightmost_predecessor_returns_none_when_no_preds_present -v`
Expected: FAIL with `ImportError: cannot import name 'find_rightmost_predecessor'`

**Step 3: Write minimal implementation**

Add to `src/wlrenv/niri/ordering.py`:

```python
from wlrenv.niri.ipc import Window

SPACER_IDENTITY = "__spacer__"


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
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/niri/test_ordering.py -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add src/wlrenv/niri/ordering.py tests/niri/test_ordering.py
git commit -m "feat(niri): add find_rightmost_predecessor with spacer support"
```

---

## Task 6: Add calculate_target_column Function

**Files:**
- Modify: `src/wlrenv/niri/ordering.py`
- Modify: `tests/niri/test_ordering.py`

**Step 1: Write the failing tests**

Add to `tests/niri/test_ordering.py`:

```python
def test_calculate_target_column_returns_1_when_no_predecessors() -> None:
    from wlrenv.niri.ordering import calculate_target_column

    saved_order = ["tmux:a"]
    current_windows: dict[str, Window] = {}

    target = calculate_target_column("tmux:a", saved_order, current_windows)

    assert target == 1


def test_calculate_target_column_returns_pred_plus_1() -> None:
    from wlrenv.niri.ordering import calculate_target_column

    saved_order = ["tmux:a", "tmux:b"]
    win_a, _ = make_test_window(1, "tmux:a", column=2)
    current_windows = {"tmux:a": win_a}

    target = calculate_target_column("tmux:b", saved_order, current_windows)

    assert target == 3  # Right of A at column 2


def test_calculate_target_column_accounts_for_spacer() -> None:
    from wlrenv.niri.ordering import SPACER_IDENTITY, calculate_target_column

    saved_order = ["tmux:a"]
    spacer, _ = make_test_window(99, SPACER_IDENTITY, column=1)
    current_windows = {SPACER_IDENTITY: spacer}

    target = calculate_target_column("tmux:a", saved_order, current_windows)

    assert target == 2  # Right of spacer at column 1
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/niri/test_ordering.py::test_calculate_target_column_returns_1_when_no_predecessors -v`
Expected: FAIL with `ImportError: cannot import name 'calculate_target_column'`

**Step 3: Write minimal implementation**

Add to `src/wlrenv/niri/ordering.py`:

```python
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
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/niri/test_ordering.py -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add src/wlrenv/niri/ordering.py tests/niri/test_ordering.py
git commit -m "feat(niri): add calculate_target_column function"
```

---

## Task 7: Add move_to_column Function

**Files:**
- Modify: `src/wlrenv/niri/ordering.py`
- Modify: `tests/niri/test_ordering.py`

**Step 1: Write the failing tests**

Add to `tests/niri/test_ordering.py`:

```python
from unittest.mock import MagicMock, call, patch


@patch("wlrenv.niri.ordering.ipc")
def test_move_to_column_does_nothing_when_already_at_target(mock_ipc: MagicMock) -> None:
    from wlrenv.niri.ordering import move_to_column

    move_to_column(window_id=1, current_column=3, target_column=3)

    mock_ipc.focus_window.assert_not_called()
    mock_ipc.move_column_left.assert_not_called()
    mock_ipc.move_column_right.assert_not_called()


@patch("wlrenv.niri.ordering.ipc")
def test_move_to_column_moves_left(mock_ipc: MagicMock) -> None:
    from wlrenv.niri.ordering import move_to_column

    move_to_column(window_id=1, current_column=5, target_column=3)

    mock_ipc.focus_window.assert_called_once_with(1)
    assert mock_ipc.move_column_left.call_count == 2
    mock_ipc.move_column_right.assert_not_called()


@patch("wlrenv.niri.ordering.ipc")
def test_move_to_column_moves_right(mock_ipc: MagicMock) -> None:
    from wlrenv.niri.ordering import move_to_column

    move_to_column(window_id=1, current_column=2, target_column=5)

    mock_ipc.focus_window.assert_called_once_with(1)
    assert mock_ipc.move_column_right.call_count == 3
    mock_ipc.move_column_left.assert_not_called()
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/niri/test_ordering.py::test_move_to_column_does_nothing_when_already_at_target -v`
Expected: FAIL with `ImportError: cannot import name 'move_to_column'`

**Step 3: Write minimal implementation**

Add to `src/wlrenv/niri/ordering.py`:

```python
from wlrenv.niri import ipc


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
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/niri/test_ordering.py -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add src/wlrenv/niri/ordering.py tests/niri/test_ordering.py
git commit -m "feat(niri): add move_to_column function"
```

---

## Task 8: Add place_window Orchestration Function

**Files:**
- Modify: `src/wlrenv/niri/ordering.py`
- Modify: `tests/niri/test_ordering.py`

**Step 1: Write the failing tests**

Add to `tests/niri/test_ordering.py`:

```python
from pathlib import Path

import pytest


@pytest.fixture
def temp_state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from wlrenv.niri import config

    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    return tmp_path


@patch("wlrenv.niri.ordering.ipc")
@patch("wlrenv.niri.ordering.order_storage")
def test_place_window_moves_to_correct_column(
    mock_storage: MagicMock,
    mock_ipc: MagicMock,
    temp_state_dir: Path,
) -> None:
    from wlrenv.niri.ordering import place_window

    # Setup: B should come after A
    mock_storage.get_order.return_value = ["tmux:a", "tmux:b"]

    # A is present at column 2
    win_a, _ = make_test_window(1, "tmux:a", column=2)
    mock_ipc.get_windows.return_value = [win_a]

    # B spawns at column 1, should move to column 3 (right of A)
    place_window(
        window_id=2,
        identity="tmux:b",
        workspace_id=1,
        current_column=1,
    )

    mock_ipc.focus_window.assert_called_once_with(2)
    assert mock_ipc.move_column_right.call_count == 2  # 1 -> 3


@patch("wlrenv.niri.ordering.ipc")
@patch("wlrenv.niri.ordering.order_storage")
def test_place_window_skips_move_when_already_correct(
    mock_storage: MagicMock,
    mock_ipc: MagicMock,
    temp_state_dir: Path,
) -> None:
    from wlrenv.niri.ordering import place_window

    mock_storage.get_order.return_value = ["tmux:a"]
    mock_ipc.get_windows.return_value = []

    # A has no predecessors, target is column 1, already at column 1
    place_window(
        window_id=1,
        identity="tmux:a",
        workspace_id=1,
        current_column=1,
    )

    mock_ipc.focus_window.assert_not_called()
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/niri/test_ordering.py::test_place_window_moves_to_correct_column -v`
Expected: FAIL with `ImportError: cannot import name 'place_window'`

**Step 3: Write minimal implementation**

Add to `src/wlrenv/niri/ordering.py`:

```python
from wlrenv.niri import order_storage


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
        # For now, use title as identity (will be refined in integration)
        # In practice, the caller will provide the correct identity mapping

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
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/niri/test_ordering.py -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add src/wlrenv/niri/ordering.py tests/niri/test_ordering.py
git commit -m "feat(niri): add place_window orchestration function"
```

---

## Task 9: Integrate Ordering into Terminal Tracking

**Files:**
- Modify: `src/wlrenv/niri/track.py`
- Modify: `tests/niri/test_track.py`

**Step 1: Write the failing test**

Add to `tests/niri/test_track.py`:

```python
@patch("wlrenv.niri.track.order_storage")
@patch("wlrenv.niri.track.storage")
@patch("wlrenv.niri.track.get_child_processes")
@patch("wlrenv.niri.track.ipc")
def test_track_terminals_saves_column_order(
    mock_ipc: MagicMock,
    mock_get_children: MagicMock,
    mock_storage: MagicMock,
    mock_order_storage: MagicMock,
    temp_state_dir: Path,
) -> None:
    from wlrenv.niri.identify import ProcessInfo
    from wlrenv.niri.track import track_terminals

    # Two tmux windows in workspace 1 at columns 2 and 1
    mock_ipc.get_windows.return_value = [
        make_window(id=1, workspace_id=1, column=2),
        make_window(id=2, workspace_id=1, column=1),
    ]
    mock_ipc.get_outputs.return_value = [MagicMock(name="eDP-1", width=3072)]
    mock_ipc.get_workspaces.return_value = [MagicMock(id=1, output="eDP-1")]
    mock_get_children.side_effect = [
        [ProcessInfo(comm="tmux", args=["tmux", "-t", "work"])],
        [ProcessInfo(comm="tmux", args=["tmux", "-t", "scratch"])],
    ]

    track_terminals()

    # Order should be saved sorted by column: scratch (col 1), work (col 2)
    mock_order_storage.save_order.assert_called_once_with(
        workspace_id=1,
        order=["tmux:scratch", "tmux:work"],
    )
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/niri/test_track.py::test_track_terminals_saves_column_order -v`
Expected: FAIL (missing order_storage import or save_order not called)

**Step 3: Write minimal implementation**

Update `src/wlrenv/niri/track.py` - add order tracking:

```python
"""Terminal window tracking."""

from __future__ import annotations

import subprocess
from collections import defaultdict

from wlrenv.niri import ipc, order_storage, storage
from wlrenv.niri.identify import ProcessInfo, identify_mosh, identify_tmux


def calculate_width_percent(tile_width: float, output_width: int) -> int:
    """Calculate width as percentage of output, rounded to nearest 10%."""
    return int((tile_width / output_width * 100 + 5) // 10 * 10)


def get_child_processes(pid: int) -> list[ProcessInfo]:
    """Get child processes of a PID."""
    try:
        result = subprocess.run(  # noqa: S603, S607
            ["pgrep", "-P", str(pid)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return []

        children = []
        for child_pid in result.stdout.strip().split("\n"):
            if not child_pid:
                continue
            # Get comm and args for each child
            comm_result = subprocess.run(  # noqa: S603, S607
                ["ps", "-o", "comm=", "-p", child_pid],
                capture_output=True,
                text=True,
                check=False,
            )
            args_result = subprocess.run(  # noqa: S603, S607
                ["ps", "-o", "args=", "-p", child_pid],
                capture_output=True,
                text=True,
                check=False,
            )
            if comm_result.returncode == 0 and args_result.returncode == 0:
                comm = comm_result.stdout.strip()
                args = args_result.stdout.strip().split()
                children.append(ProcessInfo(comm=comm, args=args))
        return children
    except Exception:
        return []


def track_terminals() -> None:
    """Track all terminal windows and store their metadata."""
    windows = ipc.get_windows(app_id="Alacritty")
    outputs = {o.name: o for o in ipc.get_outputs()}
    workspaces = {w.id: w for w in ipc.get_workspaces()}

    # Track windows per workspace for ordering
    # workspace_id -> list of (column, identity)
    workspace_windows: dict[int, list[tuple[int, str]]] = defaultdict(list)

    for window in windows:
        workspace = workspaces.get(window.workspace_id)
        if not workspace:
            continue

        output = outputs.get(workspace.output)
        if not output:
            continue

        width = calculate_width_percent(window.tile_width, output.width)

        # Identify the session
        children = get_child_processes(window.pid)
        for child in children:
            if identity := identify_tmux(child):
                storage.store_entry("tmux", identity, window.workspace_id, width)
                if window.column is not None:
                    workspace_windows[window.workspace_id].append(
                        (window.column, f"tmux:{identity}")
                    )
                break
            if identity := identify_mosh(child):
                storage.store_entry("mosh", identity, window.workspace_id, width)
                if window.column is not None:
                    workspace_windows[window.workspace_id].append(
                        (window.column, f"mosh:{identity}")
                    )
                break

    # Save column order per workspace
    for workspace_id, entries in workspace_windows.items():
        # Sort by column, extract identities
        entries.sort(key=lambda x: x[0])
        order = [identity for _, identity in entries]
        order_storage.save_order(workspace_id=workspace_id, order=order)
```

**Step 4: Update test helper and run tests**

First update `make_window` helper in `tests/niri/test_track.py` to include column:

```python
def make_window(
    id: int = 1,
    workspace_id: int = 1,
    tile_width: float = 1535.0,
    column: int = 1,
) -> MagicMock:
    return MagicMock(
        id=id,
        workspace_id=workspace_id,
        tile_width=tile_width,
        pid=1000 + id,
        column=column,
    )
```

Run: `uv run pytest tests/niri/test_track.py -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add src/wlrenv/niri/track.py tests/niri/test_track.py
git commit -m "feat(niri): integrate column order tracking into terminal tracker"
```

---

## Task 10: Integrate Ordering into Terminal Restore

**Files:**
- Modify: `src/wlrenv/niri/restore.py`
- Modify: `tests/niri/test_restore.py`

**Step 1: Write the failing test**

Add to `tests/niri/test_restore.py`:

```python
@patch("wlrenv.niri.restore.ordering")
@patch("wlrenv.niri.restore.ipc")
@patch("wlrenv.niri.restore.storage")
@patch("wlrenv.niri.restore.spawn_terminal")
@patch("wlrenv.niri.restore.get_detached_tmux_sessions")
def test_restore_tmux_places_window_in_order(
    mock_sessions: MagicMock,
    mock_spawn: MagicMock,
    mock_storage: MagicMock,
    mock_ipc: MagicMock,
    mock_ordering: MagicMock,
) -> None:
    from wlrenv.niri.restore import restore_tmux

    mock_sessions.return_value = ["work"]
    mock_storage.lookup.return_value = {"workspace": 2, "width": 50}
    mock_spawn.return_value = MagicMock(pid=1234)
    mock_ipc.wait_for_window.return_value = 42

    # Simulate window spawning at column 3
    mock_ipc.get_windows.return_value = [
        MagicMock(id=42, workspace_id=2, column=3),
    ]

    restore_tmux()

    # Should call place_window after configure
    mock_ordering.place_window.assert_called_once_with(
        window_id=42,
        identity="tmux:work",
        workspace_id=2,
        current_column=3,
    )
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/niri/test_restore.py::test_restore_tmux_places_window_in_order -v`
Expected: FAIL (ordering.place_window not called)

**Step 3: Write minimal implementation**

Update `src/wlrenv/niri/restore.py`:

```python
"""Terminal session restoration."""

from __future__ import annotations

import base64
import os
import subprocess
from pathlib import Path

from wlrenv.niri import ipc, ordering, storage


def get_detached_tmux_sessions() -> list[str]:
    """Get list of detached tmux sessions."""
    try:
        result = subprocess.run(  # noqa: S603, S607
            ["tmux", "list-sessions", "-F", "#{session_name}:#{session_attached}"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return []

        sessions = []
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            name, attached = line.rsplit(":", 1)
            if attached == "0":
                sessions.append(name)
        return sessions
    except Exception:
        return []


def read_moshen_sessions() -> list[tuple[str, str]]:
    """Read moshen sessions from state file."""
    state_home = os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state"))
    sessions_file = Path(state_home) / "moshen" / "sessions"

    if not sessions_file.exists():
        return []

    sessions = []
    for line in sessions_file.read_text().strip().split("\n"):
        if not line:
            continue
        try:
            # Format: base64(host):base64(session_name)
            host_b64, session_b64 = line.split(":")
            host = base64.b64decode(host_b64).decode()
            session = base64.b64decode(session_b64).decode()
            sessions.append((host, session))
        except Exception:
            continue
    return sessions


def spawn_terminal(args: list[str]) -> subprocess.Popen[bytes]:
    """Spawn a terminal with given arguments."""
    terminal = os.environ.get("TERMINAL", "alacritty")
    cmd = [terminal, "-e", *args]
    return subprocess.Popen(cmd)  # noqa: S603


def _get_window_column(window_id: int, workspace_id: int) -> int | None:
    """Get the current column of a window."""
    windows = ipc.get_windows()
    for w in windows:
        if w.id == window_id and w.workspace_id == workspace_id:
            return w.column
    return None


def restore_tmux() -> None:
    """Restore detached tmux sessions."""
    sessions = get_detached_tmux_sessions()

    for session in sessions:
        props = storage.lookup("tmux", session)
        proc = spawn_terminal(["tmux", "attach-session", "-t", session])

        if props:
            window_id = ipc.wait_for_window(pid=proc.pid)
            if window_id:
                workspace_id = props["workspace"]
                ipc.configure(window_id, workspace=workspace_id, width=props["width"])

                # Place window in correct column order
                column = _get_window_column(window_id, workspace_id)
                if column is not None:
                    ordering.place_window(
                        window_id=window_id,
                        identity=f"tmux:{session}",
                        workspace_id=workspace_id,
                        current_column=column,
                    )


def restore_mosh() -> None:
    """Restore moshen sessions."""
    sessions = read_moshen_sessions()

    for host, session_name in sessions:
        identity = f"{host}:{session_name}"
        props = storage.lookup("mosh", identity)
        proc = spawn_terminal(["moshen", host, session_name])

        if props:
            window_id = ipc.wait_for_window(pid=proc.pid)
            if window_id:
                workspace_id = props["workspace"]
                ipc.configure(window_id, workspace=workspace_id, width=props["width"])

                # Place window in correct column order
                column = _get_window_column(window_id, workspace_id)
                if column is not None:
                    ordering.place_window(
                        window_id=window_id,
                        identity=f"mosh:{identity}",
                        workspace_id=workspace_id,
                        current_column=column,
                    )
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/niri/test_restore.py -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add src/wlrenv/niri/restore.py tests/niri/test_restore.py
git commit -m "feat(niri): integrate column ordering into terminal restore"
```

---

## Task 11: Integrate Ordering into Librewolf Tracking and Restore

**Files:**
- Modify: `src/wlrenv/niri/librewolf_host.py`
- Modify: `tests/niri/test_librewolf_host.py`

**Step 1: Write the failing tests**

Add to `tests/niri/test_librewolf_host.py`:

```python
@patch("wlrenv.niri.librewolf_host.order_storage")
@patch("wlrenv.niri.librewolf_host.ipc")
@patch("wlrenv.niri.librewolf_host.storage")
def test_handle_store_saves_column_order(
    mock_storage: MagicMock,
    mock_ipc: MagicMock,
    mock_order_storage: MagicMock,
    temp_state_dir: Path,
) -> None:
    from wlrenv.niri.librewolf_host import handle_store

    # Two browser windows in workspace 1
    mock_ipc.find_window_by_title.side_effect = [
        MagicMock(id=1, workspace_id=1, tile_width=1500, column=2),
        MagicMock(id=2, workspace_id=1, tile_width=1500, column=1),
    ]
    mock_ipc.get_outputs.return_value = [MagicMock(name="eDP-1", width=3000)]
    mock_ipc.get_workspaces.return_value = [MagicMock(id=1, output="eDP-1")]

    message = {
        "windows": [
            {"title": "Window A", "urls": ["https://a.com"]},
            {"title": "Window B", "urls": ["https://b.com"]},
        ]
    }

    handle_store(message, request_id=None)

    # Order should be saved by column
    mock_order_storage.save_order.assert_called()


@patch("wlrenv.niri.librewolf_host.ordering")
@patch("wlrenv.niri.librewolf_host.ipc")
@patch("wlrenv.niri.librewolf_host.storage")
def test_handle_restore_places_windows(
    mock_storage: MagicMock,
    mock_ipc: MagicMock,
    mock_ordering: MagicMock,
    temp_state_dir: Path,
) -> None:
    from wlrenv.niri.librewolf_host import handle_restore

    mock_ipc.find_window_by_title.return_value = MagicMock(
        id=1, workspace_id=2, tile_width=1500, column=3
    )
    mock_ipc.get_outputs.return_value = [MagicMock(name="eDP-1", width=3000)]
    mock_ipc.get_workspaces.return_value = [MagicMock(id=2, output="eDP-1")]
    mock_storage.lookup.return_value = {"workspace": 2, "width": 50}

    message = {
        "windows": [
            {"title": "Window A", "urls": ["https://a.com"]},
        ]
    }

    handle_restore(message, request_id=None)

    mock_ordering.place_window.assert_called_once()
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/niri/test_librewolf_host.py::test_handle_store_saves_column_order -v`
Expected: FAIL (order_storage not imported or save_order not called)

**Step 3: Write minimal implementation**

Update `src/wlrenv/niri/librewolf_host.py` to add ordering support. Key changes:

1. Import `order_storage` and `ordering`
2. In `handle_store`: collect column positions, save order per workspace
3. In `handle_restore`: call `ordering.place_window` after configure

The implementation mirrors the terminal tracking/restore pattern.

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/niri/test_librewolf_host.py -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add src/wlrenv/niri/librewolf_host.py tests/niri/test_librewolf_host.py
git commit -m "feat(niri): integrate column ordering into librewolf tracking/restore"
```

---

## Task 12: Run Full Test Suite and Verify

**Files:**
- All test files

**Step 1: Run full test suite**

Run: `uv run pytest tests/niri/ -v`
Expected: All tests PASS

**Step 2: Run type checker**

Run: `uv run pyright src/wlrenv/niri/`
Expected: No errors

**Step 3: Run linter**

Run: `uv run ruff check src/wlrenv/niri/`
Expected: No errors (or only expected warnings)

**Step 4: Format code**

Run: `uv run ruff format src/wlrenv/niri/ tests/niri/`

**Step 5: Final commit if any formatting changes**

```bash
git add -A
git commit -m "chore(niri): format and lint fixes"
```

---

## Summary

This plan adds window column ordering to the niri tracking system through:

1. **IPC extensions** (Tasks 1-2): Add column/row to Window, add focus/move functions
2. **Order storage** (Task 3): New `orders.json` file for workspace column orders
3. **Ordering algorithm** (Tasks 4-8): Predecessor tracking, target calculation, movement
4. **Integration** (Tasks 9-11): Wire ordering into track/restore for terminals and librewolf
5. **Verification** (Task 12): Full test suite, type checking, linting

The algorithm positions each window relative to its rightmost present predecessor (including spacer as implicit predecessor), achieving self-correcting ordering regardless of spawn/processing order.
