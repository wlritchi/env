"""Track terminal windows and store their metadata."""

from __future__ import annotations

import subprocess
from collections import defaultdict

from wlrenv.niri import ipc, order_storage
from wlrenv.niri.identify import ProcessInfo, identify_mosh, identify_tmux
from wlrenv.niri.storage import store_entry


def calculate_width_percent(tile_width: float, output_width: int) -> int:
    """Calculate width as percentage of output, rounded to nearest 10%."""
    pct = tile_width / output_width * 100
    return int((pct + 5) // 10 * 10)


def get_child_processes(pid: int) -> list[ProcessInfo]:
    """Get child processes of given PID."""
    try:
        result = subprocess.run(  # noqa: S603
            ["pgrep", "-P", str(pid)],  # noqa: S607
            capture_output=True,
            text=True,
        )
        child_pids = [int(p) for p in result.stdout.strip().split() if p]
    except (subprocess.CalledProcessError, ValueError):
        return []

    children = []
    for cpid in child_pids:
        try:
            # Get comm (process name)
            comm_result = subprocess.run(  # noqa: S603
                ["ps", "-o", "comm=", "-p", str(cpid)],  # noqa: S607
                capture_output=True,
                text=True,
            )
            comm = comm_result.stdout.strip()

            # Get full args
            args_result = subprocess.run(  # noqa: S603
                ["ps", "-o", "args=", "-p", str(cpid)],  # noqa: S607
                capture_output=True,
                text=True,
            )
            args = args_result.stdout.strip().split()

            if comm:
                children.append(ProcessInfo(comm=comm, args=args))
        except subprocess.CalledProcessError:
            continue

    return children


def track_terminals() -> None:
    """Track all terminal windows and store their workspace/width."""
    windows = ipc.get_windows(app_id="Alacritty")
    outputs = {o.name: o for o in ipc.get_outputs()}
    workspaces = {w.id: w for w in ipc.get_workspaces()}

    # Track windows per workspace for ordering
    # workspace_id -> list of (column, identity)
    workspace_windows: dict[int, list[tuple[int, str]]] = defaultdict(list)

    for window in windows:
        # Get output for this window's workspace
        ws = workspaces.get(window.workspace_id)
        if not ws:
            continue
        output = outputs.get(ws.output)
        if not output:
            continue

        width_percent = calculate_width_percent(window.tile_width, output.width)

        # Check child processes for known apps
        children = get_child_processes(window.pid)
        for child in children:
            if identity := identify_tmux(child):
                store_entry("tmux", identity, window.workspace_id, width_percent)
                if window.column is not None:
                    workspace_windows[window.workspace_id].append(
                        (window.column, f"tmux:{identity}")
                    )
                break
            if identity := identify_mosh(child):
                store_entry("mosh", identity, window.workspace_id, width_percent)
                if window.column is not None:
                    workspace_windows[window.workspace_id].append(
                        (window.column, f"mosh:{identity}")
                    )
                break

    # Save column order per workspace
    for workspace_id, entries in workspace_windows.items():
        # Sort by column, extract identities
        entries.sort(key=lambda x: x[0])
        order = [identity for _, identity in entries]
        order_storage.save_order(workspace_id=workspace_id, order=order)
