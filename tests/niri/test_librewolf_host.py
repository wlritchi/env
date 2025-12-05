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


@patch("wlrenv.niri.librewolf_host.UrlMatcher")
@patch("wlrenv.niri.librewolf_host.order_storage")
@patch("wlrenv.niri.librewolf_host.store_entry")
@patch("wlrenv.niri.librewolf_host.ipc")
def test_handle_store_saves_column_order(
    mock_ipc: MagicMock,
    mock_store_entry: MagicMock,
    mock_order_storage: MagicMock,
    mock_url_matcher_cls: MagicMock,
    temp_state_dir: Path,
) -> None:
    from wlrenv.niri.librewolf_host import handle_store

    # Create output and workspace mocks (configure attributes properly)
    output = MagicMock()
    output.name = "eDP-1"
    output.width = 3000

    workspace = MagicMock()
    workspace.id = 1
    workspace.output = "eDP-1"

    # Mock UrlMatcher
    mock_matcher = MagicMock()
    mock_matcher.match_or_create.side_effect = ["uuid-a", "uuid-b"]
    mock_url_matcher_cls.load.return_value = mock_matcher

    # Two browser windows in workspace 1
    mock_ipc.find_window_by_title.side_effect = [
        MagicMock(id=1, workspace_id=1, tile_width=1500, column=2),
        MagicMock(id=2, workspace_id=1, tile_width=1500, column=1),
    ]
    mock_ipc.get_outputs.return_value = [output]
    mock_ipc.get_workspaces.return_value = [workspace]

    message = {
        "windows": [
            {"window_title": "Window A", "tabs": [{"url": "https://a.com"}]},
            {"window_title": "Window B", "tabs": [{"url": "https://b.com"}]},
        ]
    }

    handle_store(message, request_id=None)

    # Order should be saved by column (sorted: uuid-b at col 1, uuid-a at col 2)
    mock_order_storage.save_order.assert_called_once_with(
        workspace_id=1, order=["librewolf:uuid-b", "librewolf:uuid-a"]
    )


@patch("wlrenv.niri.librewolf_host.UrlMatcher")
@patch("wlrenv.niri.librewolf_host.ordering")
@patch("wlrenv.niri.librewolf_host.lookup")
@patch("wlrenv.niri.librewolf_host.ipc")
def test_handle_restore_places_windows(
    mock_ipc: MagicMock,
    mock_lookup: MagicMock,
    mock_ordering: MagicMock,
    mock_url_matcher_cls: MagicMock,
    temp_state_dir: Path,
) -> None:
    from wlrenv.niri.librewolf_host import handle_restore

    # Mock UrlMatcher
    mock_matcher = MagicMock()
    mock_matcher.match_or_create.return_value = "uuid-a"
    mock_url_matcher_cls.load.return_value = mock_matcher

    mock_ipc.find_window_by_title.return_value = MagicMock(
        id=1, workspace_id=2, tile_width=1500, column=3
    )
    mock_ipc.get_outputs.return_value = [MagicMock(name="eDP-1", width=3000)]
    mock_ipc.get_workspaces.return_value = [MagicMock(id=2, output="eDP-1")]
    mock_lookup.return_value = {"workspace": 2, "width": 50}

    message = {
        "windows": [
            {"window_title": "Window A", "tabs": [{"url": "https://a.com"}]},
        ]
    }

    handle_restore(message, request_id=None)

    mock_ordering.place_window.assert_called_once_with(
        window_id=1,
        identity="librewolf:uuid-a",
        workspace_id=2,
        current_column=3,
    )
