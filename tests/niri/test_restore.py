from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from wlrenv.niri.restore import restore_mosh, restore_tmux


@pytest.fixture
def temp_state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Use temporary directory for state."""
    import wlrenv.niri.config as config

    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    return tmp_path


@patch("wlrenv.niri.restore.spawn_terminal")
@patch("wlrenv.niri.restore.get_detached_tmux_sessions")
@patch("wlrenv.niri.ipc.wait_for_window")
@patch("wlrenv.niri.ipc.configure")
def test_restore_tmux_spawns_and_configures(
    mock_configure: MagicMock,
    mock_wait: MagicMock,
    mock_sessions: MagicMock,
    mock_spawn: MagicMock,
    temp_state_dir: Path,
) -> None:
    from wlrenv.niri.storage import store_entry

    # Set up stored data
    store_entry("tmux", "work", workspace=2, width=70)

    # Mock session list
    mock_sessions.return_value = ["work"]

    # Mock spawn returning a process with PID
    mock_proc = MagicMock()
    mock_proc.pid = 12345
    mock_spawn.return_value = mock_proc

    # Mock window appearing
    mock_wait.return_value = 42

    restore_tmux()

    mock_spawn.assert_called_once()
    mock_wait.assert_called_once_with(pid=12345)
    mock_configure.assert_called_once_with(42, workspace=2, width=70)


@patch("wlrenv.niri.restore.spawn_terminal")
@patch("wlrenv.niri.restore.get_detached_tmux_sessions")
@patch("wlrenv.niri.ipc.wait_for_window")
@patch("wlrenv.niri.ipc.configure")
def test_restore_tmux_skips_wait_if_no_props(
    mock_configure: MagicMock,
    mock_wait: MagicMock,
    mock_sessions: MagicMock,
    mock_spawn: MagicMock,
    temp_state_dir: Path,
) -> None:
    # No stored data for this session
    mock_sessions.return_value = ["unknown"]
    mock_proc = MagicMock()
    mock_proc.pid = 12345
    mock_spawn.return_value = mock_proc

    restore_tmux()

    mock_spawn.assert_called_once()
    mock_wait.assert_not_called()
    mock_configure.assert_not_called()


@patch("wlrenv.niri.restore.spawn_terminal")
@patch("wlrenv.niri.restore.read_moshen_sessions")
@patch("wlrenv.niri.ipc.wait_for_window")
@patch("wlrenv.niri.ipc.configure")
def test_restore_mosh_spawns_and_configures(
    mock_configure: MagicMock,
    mock_wait: MagicMock,
    mock_sessions: MagicMock,
    mock_spawn: MagicMock,
    temp_state_dir: Path,
) -> None:
    from wlrenv.niri.storage import store_entry

    # Set up stored data
    store_entry("mosh", "server.example.com:session1", workspace=3, width=80)

    # Mock session list
    mock_sessions.return_value = [("server.example.com", "session1")]

    # Mock spawn returning a process with PID
    mock_proc = MagicMock()
    mock_proc.pid = 12345
    mock_spawn.return_value = mock_proc

    # Mock window appearing
    mock_wait.return_value = 42

    restore_mosh()

    mock_spawn.assert_called_once()
    mock_wait.assert_called_once_with(pid=12345)
    mock_configure.assert_called_once_with(42, workspace=3, width=80)


@patch("wlrenv.niri.restore.spawn_terminal")
@patch("wlrenv.niri.restore.read_moshen_sessions")
@patch("wlrenv.niri.ipc.wait_for_window")
@patch("wlrenv.niri.ipc.configure")
def test_restore_mosh_skips_wait_if_no_props(
    mock_configure: MagicMock,
    mock_wait: MagicMock,
    mock_sessions: MagicMock,
    mock_spawn: MagicMock,
    temp_state_dir: Path,
) -> None:
    # No stored data for this session
    mock_sessions.return_value = [("unknown.example.com", "unknown")]
    mock_proc = MagicMock()
    mock_proc.pid = 12345
    mock_spawn.return_value = mock_proc

    restore_mosh()

    mock_spawn.assert_called_once()
    mock_wait.assert_not_called()
    mock_configure.assert_not_called()


@patch("wlrenv.niri.restore.ordering")
@patch("wlrenv.niri.restore.lookup")
@patch("wlrenv.niri.restore.ipc")
@patch("wlrenv.niri.restore.spawn_terminal")
@patch("wlrenv.niri.restore.get_detached_tmux_sessions")
def test_restore_tmux_places_window_in_order(
    mock_sessions: MagicMock,
    mock_spawn: MagicMock,
    mock_ipc: MagicMock,
    mock_lookup: MagicMock,
    mock_ordering: MagicMock,
) -> None:
    mock_sessions.return_value = ["work"]
    mock_lookup.return_value = {"workspace": 2, "width": 50}
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


@patch("wlrenv.niri.restore.ordering")
@patch("wlrenv.niri.restore.lookup")
@patch("wlrenv.niri.restore.ipc")
@patch("wlrenv.niri.restore.spawn_terminal")
@patch("wlrenv.niri.restore.read_moshen_sessions")
def test_restore_mosh_places_window_in_order(
    mock_sessions: MagicMock,
    mock_spawn: MagicMock,
    mock_ipc: MagicMock,
    mock_lookup: MagicMock,
    mock_ordering: MagicMock,
) -> None:
    mock_sessions.return_value = [("server.example.com", "session1")]
    mock_lookup.return_value = {"workspace": 3, "width": 80}
    mock_spawn.return_value = MagicMock(pid=5678)
    mock_ipc.wait_for_window.return_value = 99

    # Simulate window spawning at column 1
    mock_ipc.get_windows.return_value = [
        MagicMock(id=99, workspace_id=3, column=1),
    ]

    restore_mosh()

    # Should call place_window after configure
    mock_ordering.place_window.assert_called_once_with(
        window_id=99,
        identity="mosh:server.example.com:session1",
        workspace_id=3,
        current_column=1,
    )
