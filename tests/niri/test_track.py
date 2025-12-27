from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


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


def test_calculate_width_percent_rounds_to_10() -> None:
    from wlrenv.niri.track import calculate_width_percent

    assert calculate_width_percent(500, 1000) == 50
    assert calculate_width_percent(333, 1000) == 30
    assert calculate_width_percent(666, 1000) == 70
    assert calculate_width_percent(450, 1000) == 50


def test_track_terminals_stores_tmux_session(temp_dirs: tuple[Path, Path]) -> None:
    from wlrenv.niri import positions
    from wlrenv.niri.track import track_terminals

    mock_window = MagicMock()
    mock_window.id = 123
    mock_window.pid = 1000
    mock_window.workspace_id = 1
    mock_window.tile_width = 500
    mock_window.column = 2

    mock_output = MagicMock()
    mock_output.name = "eDP-1"
    mock_output.width = 1000

    mock_workspace = MagicMock()
    mock_workspace.id = 1
    mock_workspace.output = "eDP-1"

    with (
        patch("wlrenv.niri.track.ipc.get_windows", return_value=[mock_window]),
        patch("wlrenv.niri.track.ipc.get_outputs", return_value=[mock_output]),
        patch("wlrenv.niri.track.ipc.get_workspaces", return_value=[mock_workspace]),
        patch("wlrenv.niri.track.get_child_processes") as mock_children,
        patch("wlrenv.niri.track.identify_tmux", return_value="dotfiles"),
    ):
        mock_children.return_value = [MagicMock(comm="tmux", args=["tmux"])]
        track_terminals()

    data = positions.load_positions()
    boot_id = next(iter(data.boots.keys()))
    entries = data.boots[boot_id].workspaces["1"]

    assert len(entries) == 1
    assert entries[0].id == "tmux:dotfiles"
    assert entries[0].width == 50


def test_track_terminals_saves_column_order(temp_dirs: tuple[Path, Path]) -> None:
    from wlrenv.niri import positions
    from wlrenv.niri.track import track_terminals

    mock_windows = [
        MagicMock(id=1, pid=100, workspace_id=1, tile_width=500, column=3),
        MagicMock(id=2, pid=200, workspace_id=1, tile_width=500, column=1),
    ]

    mock_output = MagicMock()
    mock_output.name = "eDP-1"
    mock_output.width = 1000
    mock_workspace = MagicMock(id=1, output="eDP-1")

    with (
        patch("wlrenv.niri.track.ipc.get_windows", return_value=mock_windows),
        patch("wlrenv.niri.track.ipc.get_outputs", return_value=[mock_output]),
        patch("wlrenv.niri.track.ipc.get_workspaces", return_value=[mock_workspace]),
        patch("wlrenv.niri.track.get_child_processes") as mock_children,
        patch("wlrenv.niri.track.identify_tmux", side_effect=["a", "b"]),
        patch("wlrenv.niri.track.identify_mosh", return_value=None),
    ):
        mock_children.return_value = [MagicMock(comm="tmux", args=["tmux"])]
        track_terminals()

    data = positions.load_positions()
    boot_id = next(iter(data.boots.keys()))
    entries = data.boots[boot_id].workspaces["1"]

    # Entries preserve column index from windows
    ids_with_index = [(e.id, e.index) for e in entries]
    assert ("tmux:a", 3) in ids_with_index
    assert ("tmux:b", 1) in ids_with_index
