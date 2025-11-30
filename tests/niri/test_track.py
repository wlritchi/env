from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from wlrenv.niri.ipc import Output, Window, Workspace
from wlrenv.niri.track import (
    calculate_width_percent,
    track_terminals,
)


@pytest.fixture
def temp_state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Use temporary directory for state."""
    import wlrenv.niri.config as config

    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    return tmp_path


def test_calculate_width_percent_rounds_to_10() -> None:
    # 1535 / 3072 = 49.97% -> rounds to 50
    assert calculate_width_percent(1535.0, 3072) == 50

    # 3070 / 3072 = 99.9% -> rounds to 100
    assert calculate_width_percent(3070.0, 3072) == 100

    # 768 / 3072 = 25% -> exactly 30 (rounds up from 25)
    assert calculate_width_percent(768.0, 3072) == 30


def make_window(
    id: int = 1,
    pid: int = 1234,
    workspace_id: int = 1,
    tile_width: float = 1535.0,
) -> Window:
    return Window(
        id=id,
        title="Alacritty",
        app_id="Alacritty",
        pid=pid,
        workspace_id=workspace_id,
        tile_width=tile_width,
        tile_height=1000.0,
    )


@patch("wlrenv.niri.track.get_child_processes")
@patch("wlrenv.niri.ipc.get_workspaces")
@patch("wlrenv.niri.ipc.get_outputs")
@patch("wlrenv.niri.ipc.get_windows")
def test_track_terminals_stores_tmux_session(
    mock_windows: MagicMock,
    mock_outputs: MagicMock,
    mock_workspaces: MagicMock,
    mock_children: MagicMock,
    temp_state_dir: Path,
) -> None:
    from wlrenv.niri.identify import ProcessInfo
    from wlrenv.niri.storage import lookup

    mock_windows.return_value = [make_window(id=1, pid=1000, workspace_id=2)]
    mock_outputs.return_value = [Output(name="eDP-1", width=3072, height=1920)]
    mock_workspaces.return_value = [Workspace(id=2, output="eDP-1")]
    mock_children.return_value = [
        ProcessInfo(
            comm="tmux: client", args=["tmux", "attach-session", "-t", "mywork"]
        )
    ]

    track_terminals()

    result = lookup("tmux", "mywork")
    assert result is not None
    assert result["workspace"] == 2
    assert result["width"] == 50
