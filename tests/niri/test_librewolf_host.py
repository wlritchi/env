# tests/niri/test_librewolf_host.py
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from wlrenv.niri.librewolf_host import handle_message


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


@patch("wlrenv.niri.librewolf_host.ipc")
def test_handle_store_message(
    mock_ipc: MagicMock, temp_dirs: tuple[Path, Path]
) -> None:
    from wlrenv.niri.ipc import Window

    mock_ipc.find_window_by_title.return_value = Window(
        id=42,
        title="GitHub",
        app_id="librewolf",
        pid=1234,
        workspace_id=2,
        tile_width=1535.0,
        tile_height=1000.0,
        column=1,
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
def test_handle_restore_message(
    mock_ipc: MagicMock, temp_dirs: tuple[Path, Path]
) -> None:
    from wlrenv.niri import positions
    from wlrenv.niri.ipc import Window
    from wlrenv.niri.librewolf import UrlMatcher

    # Set up stored data
    matcher = UrlMatcher.load()
    uuid = matcher.match_or_create(["https://github.com"])
    matcher.save()

    # Store position using positions module
    positions.save_positions(
        {
            "version": 1,
            "boots": {
                "test-boot": {
                    "updated_at": "2025-12-26T10:00:00Z",
                    "apps": ["librewolf"],
                    "workspaces": {
                        "3": [
                            {
                                "id": f"librewolf:{uuid}",
                                "index": 1,
                                "window_id": 100,
                                "width": 70,
                            }
                        ]
                    },
                }
            },
        }
    )

    mock_ipc.find_window_by_title.return_value = Window(
        id=42,
        title="GitHub",
        app_id="librewolf",
        pid=1234,
        workspace_id=1,
        tile_width=1535.0,
        tile_height=1000.0,
        column=1,
    )
    mock_ipc.get_windows.return_value = [
        MagicMock(id=42, workspace_id=3, column=1),
    ]

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


def test_handle_ping_message(temp_dirs: tuple[Path, Path]) -> None:
    message = {"action": "ping"}
    response = handle_message(message)
    assert response["success"] is True


@patch("wlrenv.niri.librewolf_host.UrlMatcher")
@patch("wlrenv.niri.librewolf_host.ipc")
def test_handle_store_saves_column_order(
    mock_ipc: MagicMock,
    mock_url_matcher_cls: MagicMock,
    temp_dirs: tuple[Path, Path],
) -> None:
    from wlrenv.niri import positions
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

    # Check positions were saved
    data = positions.load_positions()
    boot_id = next(iter(data["boots"].keys()))
    entries = data["boots"][boot_id]["workspaces"]["1"]

    # Should have both entries with correct column indices
    entry_ids = {e["id"] for e in entries}
    assert entry_ids == {"librewolf:uuid-a", "librewolf:uuid-b"}

    # Verify column indices
    entry_by_id = {e["id"]: e for e in entries}
    assert entry_by_id["librewolf:uuid-a"]["index"] == 2
    assert entry_by_id["librewolf:uuid-b"]["index"] == 1


@patch("wlrenv.niri.librewolf_host.UrlMatcher")
@patch("wlrenv.niri.librewolf_host.ordering")
@patch("wlrenv.niri.librewolf_host.ipc")
def test_handle_restore_places_windows(
    mock_ipc: MagicMock,
    mock_ordering: MagicMock,
    mock_url_matcher_cls: MagicMock,
    temp_dirs: tuple[Path, Path],
) -> None:
    from wlrenv.niri import positions
    from wlrenv.niri.librewolf_host import handle_restore

    # Mock UrlMatcher
    mock_matcher = MagicMock()
    mock_matcher.match_or_create.return_value = "uuid-a"
    mock_url_matcher_cls.load.return_value = mock_matcher

    # Set up stored position
    positions.save_positions(
        {
            "version": 1,
            "boots": {
                "test-boot": {
                    "updated_at": "2025-12-26T10:00:00Z",
                    "apps": ["librewolf"],
                    "workspaces": {
                        "2": [
                            {
                                "id": "librewolf:uuid-a",
                                "index": 1,
                                "window_id": 100,
                                "width": 50,
                            }
                        ]
                    },
                }
            },
        }
    )

    mock_ipc.find_window_by_title.return_value = MagicMock(
        id=1, workspace_id=2, tile_width=1500, column=3
    )
    mock_ipc.get_outputs.return_value = [MagicMock(name="eDP-1", width=3000)]
    mock_ipc.get_workspaces.return_value = [MagicMock(id=2, output="eDP-1")]

    # Mock get_windows to return the window state after configure
    mock_ipc.get_windows.return_value = [
        MagicMock(id=1, workspace_id=2, column=3),
    ]

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
