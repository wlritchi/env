# tests/niri/test_librewolf_host.py
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from wlrenv.niri.librewolf_host import handle_message


@pytest.fixture
def temp_state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Use temporary directory for state."""
    import wlrenv.niri.config as config

    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    return tmp_path


@patch("wlrenv.niri.librewolf_host.ipc")
def test_handle_store_message(mock_ipc: MagicMock, temp_state_dir: Path) -> None:
    from wlrenv.niri.ipc import Window

    mock_ipc.find_window_by_title.return_value = Window(
        id=42,
        title="GitHub",
        app_id="librewolf",
        pid=1234,
        workspace_id=2,
        tile_width=1535.0,
        tile_height=1000.0,
    )
    mock_ipc.get_outputs.return_value = [MagicMock(name="eDP-1", width=3072)]
    mock_ipc.get_workspaces.return_value = [MagicMock(id=2, output="eDP-1")]

    message = {
        "action": "store_mappings_batch",
        "windows": [
            {
                "window_title": "GitHub",
                "tabs": [{"url": "https://github.com", "title": "GitHub"}],
            }
        ],
    }

    response = handle_message(message)

    assert response["success"] is True


@patch("wlrenv.niri.librewolf_host.ipc")
def test_handle_restore_message(mock_ipc: MagicMock, temp_state_dir: Path) -> None:
    from wlrenv.niri.ipc import Window
    from wlrenv.niri.librewolf import UrlMatcher
    from wlrenv.niri.storage import store_entry

    # Set up stored data
    matcher = UrlMatcher.load()
    uuid = matcher.match_or_create(["https://github.com"])
    matcher.save()
    store_entry("librewolf", uuid, workspace=3, width=70)

    mock_ipc.find_window_by_title.return_value = Window(
        id=42,
        title="GitHub",
        app_id="librewolf",
        pid=1234,
        workspace_id=1,
        tile_width=1535.0,
        tile_height=1000.0,
    )

    message = {
        "action": "restore_workspaces",
        "windows": [
            {
                "window_title": "GitHub",
                "tabs": [{"url": "https://github.com", "title": "GitHub"}],
            }
        ],
    }

    response = handle_message(message)

    assert response["success"] is True
    mock_ipc.configure.assert_called_once()


def test_handle_ping_message(temp_state_dir: Path) -> None:
    message = {"action": "ping"}
    response = handle_message(message)
    assert response["success"] is True
