# tests/niri/test_cli.py
from __future__ import annotations

from unittest.mock import MagicMock, patch

from wlrenv.niri.cli import restore_mosh_cli, restore_tmux_cli, track_terminals_cli


@patch("wlrenv.niri.cli.track_terminals")
def test_track_terminals_cli_calls_track(mock_track: MagicMock) -> None:
    track_terminals_cli()
    mock_track.assert_called_once()


@patch("wlrenv.niri.cli.restore_tmux")
def test_restore_tmux_cli_calls_restore(mock_restore: MagicMock) -> None:
    restore_tmux_cli()
    mock_restore.assert_called_once()


@patch("wlrenv.niri.cli.restore_mosh")
def test_restore_mosh_cli_calls_restore(mock_restore: MagicMock) -> None:
    restore_mosh_cli()
    mock_restore.assert_called_once()
