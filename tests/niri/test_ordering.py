# tests/niri/test_ordering.py
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from wlrenv.niri.ipc import Window


@pytest.fixture
def temp_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Set up temp state and run directories."""
    from wlrenv.niri import config

    state_dir = tmp_path / "state"
    run_dir = tmp_path / "run"
    state_dir.mkdir()
    run_dir.mkdir()

    monkeypatch.setattr(config, "STATE_DIR", state_dir)
    monkeypatch.setattr("wlrenv.niri.positions._get_run_dir", lambda: run_dir)
    return state_dir, run_dir


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


@patch("wlrenv.niri.ordering.ipc")
def test_move_to_column_does_nothing_when_already_at_target(
    mock_ipc: MagicMock,
) -> None:
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


@patch("wlrenv.niri.ordering.ipc")
@patch("wlrenv.niri.ordering.positions")
def test_place_window_moves_to_correct_column(
    mock_positions: MagicMock,
    mock_ipc: MagicMock,
    temp_dirs: tuple[Path, Path],
) -> None:
    from wlrenv.niri.ordering import place_window

    # Setup: B should come after A (A is a predecessor of B)
    mock_positions.find_predecessors.return_value = ["tmux:a"]
    mock_positions.resolve_predecessors_to_window_ids.return_value = [1]  # window_id 1

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
@patch("wlrenv.niri.ordering.positions")
def test_place_window_skips_move_when_already_correct(
    mock_positions: MagicMock,
    mock_ipc: MagicMock,
    temp_dirs: tuple[Path, Path],
) -> None:
    from wlrenv.niri.ordering import place_window

    # No predecessors
    mock_positions.find_predecessors.return_value = []
    mock_positions.resolve_predecessors_to_window_ids.return_value = []
    mock_ipc.get_windows.return_value = []

    # A has no predecessors, target is column 1, already at column 1
    place_window(
        window_id=1,
        identity="tmux:a",
        workspace_id=1,
        current_column=1,
    )

    mock_ipc.focus_window.assert_not_called()
