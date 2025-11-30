"""niri IPC wrapper for window management."""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass
from typing import Any


class NiriError(Exception):
    """Error communicating with niri."""


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


@dataclass
class Output:
    """A niri output (monitor)."""

    name: str
    width: int
    height: int


@dataclass
class Workspace:
    """A niri workspace."""

    id: int
    output: str


def _run_niri_msg(args: list[str], *, json_output: bool = True) -> Any:  # noqa: ANN401
    """Run niri msg command and return parsed output."""
    if not os.environ.get("NIRI_SOCKET"):
        raise NiriError("NIRI_SOCKET not set")

    cmd = ["niri", "msg"]
    if json_output:
        cmd.append("--json")
    cmd.extend(args)

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)  # noqa: S603
    except subprocess.CalledProcessError as e:
        raise NiriError(f"niri msg failed: {e.stderr}") from e

    if json_output and result.stdout.strip():
        return json.loads(result.stdout)
    return None


def get_windows(app_id: str | None = None) -> list[Window]:
    """Get all windows, optionally filtered by app_id."""
    data = _run_niri_msg(["windows"])

    windows = []
    for w in data:
        if app_id and w.get("app_id") != app_id:
            continue
        windows.append(
            Window(
                id=w["id"],
                title=w.get("title", ""),
                app_id=w.get("app_id", ""),
                pid=w["pid"],
                workspace_id=w["workspace_id"],
                tile_width=w["layout"]["tile_size"][0],
                tile_height=w["layout"]["tile_size"][1],
            )
        )
    return windows


def get_outputs() -> list[Output]:
    """Get all outputs with logical dimensions."""
    data = _run_niri_msg(["outputs"])

    outputs = []
    for o in data:
        outputs.append(
            Output(
                name=o["name"],
                width=o["logical"]["width"],
                height=o["logical"]["height"],
            )
        )
    return outputs


def get_workspaces() -> list[Workspace]:
    """Get all workspaces with output mapping."""
    data = _run_niri_msg(["workspaces"])

    return [Workspace(id=w["id"], output=w["output"]) for w in data]


def find_window_by_title(title: str) -> Window | None:
    """Find a window by exact title match."""
    windows = get_windows()
    for w in windows:
        if w.title == title:
            return w
    return None


def find_window_by_pid(pid: int) -> Window | None:
    """Find a window by PID."""
    windows = get_windows()
    for w in windows:
        if w.pid == pid:
            return w
    return None


def wait_for_window(pid: int, timeout: float = 5.0) -> int | None:
    """Wait for a window with given PID to appear, return window_id or None."""
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        window = find_window_by_pid(pid)
        if window:
            return window.id
        time.sleep(0.1)
    return None


def configure(window_id: int, workspace: int | None, width: int | None) -> None:
    """Configure window workspace and/or width."""
    if workspace is not None:
        _run_niri_msg(
            [
                "action",
                "move-window-to-workspace",
                "--window-id",
                str(window_id),
                "--focus",
                "false",
                str(workspace),
            ],
            json_output=False,
        )

    if width is not None:
        _run_niri_msg(
            ["action", "set-window-width", "--id", str(window_id), f"{width}%"],
            json_output=False,
        )
