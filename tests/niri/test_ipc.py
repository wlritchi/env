# tests/niri/test_ipc.py
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from wlrenv.niri.ipc import (
    configure,
    get_outputs,
    get_windows,
)


def make_window_json(
    id: int = 1,
    title: str = "Test",
    app_id: str = "Alacritty",
    pid: int = 1234,
    workspace_id: int = 1,
    tile_width: float = 1535.0,
) -> dict[str, Any]:
    return {
        "id": id,
        "title": title,
        "app_id": app_id,
        "pid": pid,
        "workspace_id": workspace_id,
        "is_focused": False,
        "is_floating": False,
        "is_urgent": False,
        "layout": {
            "pos_in_scrolling_layout": [1, 1],
            "tile_size": [tile_width, 1000.0],
            "window_size": [int(tile_width) - 2, 998],
            "tile_pos_in_workspace_view": None,
            "window_offset_in_tile": [1.0, 1.0],
        },
    }


def make_output_json(
    name: str = "eDP-1",
    width: int = 3072,
    height: int = 1920,
) -> dict[str, Any]:
    return {
        "name": name,
        "make": "Test",
        "model": "Test",
        "serial": None,
        "physical_size": [340, 220],
        "modes": [
            {
                "width": width,
                "height": height,
                "refresh_rate": 60000,
                "is_preferred": True,
            }
        ],
        "current_mode": 0,
        "vrr_supported": False,
        "vrr_enabled": False,
        "logical": {
            "x": 0,
            "y": 0,
            "width": width,
            "height": height,
            "scale": 1.0,
            "transform": "Normal",
        },
    }


@patch("wlrenv.niri.ipc._run_niri_msg")
def test_get_windows_parses_response(mock_run: MagicMock) -> None:
    mock_run.return_value = [make_window_json(id=1, title="Test Window")]

    windows = get_windows()

    assert len(windows) == 1
    assert windows[0].id == 1
    assert windows[0].title == "Test Window"


@patch("wlrenv.niri.ipc._run_niri_msg")
def test_get_windows_filters_by_app_id(mock_run: MagicMock) -> None:
    mock_run.return_value = [
        make_window_json(id=1, app_id="Alacritty"),
        make_window_json(id=2, app_id="firefox"),
    ]

    windows = get_windows(app_id="Alacritty")

    assert len(windows) == 1
    assert windows[0].id == 1


@patch("wlrenv.niri.ipc._run_niri_msg")
def test_get_outputs_parses_response(mock_run: MagicMock) -> None:
    mock_run.return_value = [make_output_json(name="eDP-1", width=3072)]

    outputs = get_outputs()

    assert len(outputs) == 1
    assert outputs[0].name == "eDP-1"
    assert outputs[0].width == 3072


@patch("wlrenv.niri.ipc._run_niri_msg")
def test_configure_calls_correct_actions(mock_run: MagicMock) -> None:
    mock_run.return_value = None

    configure(window_id=42, workspace=2, width=50)

    calls = mock_run.call_args_list
    assert len(calls) == 2
    # Check workspace action
    assert "move-window-to-workspace" in str(calls[0])
    # Check width action
    assert "set-window-width" in str(calls[1])


@patch("wlrenv.niri.ipc._run_niri_msg")
def test_configure_skips_none_values(mock_run: MagicMock) -> None:
    mock_run.return_value = None

    configure(window_id=42, workspace=2, width=None)

    calls = mock_run.call_args_list
    assert len(calls) == 1
    assert "move-window-to-workspace" in str(calls[0])
