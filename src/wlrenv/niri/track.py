"""Track terminal windows and store their metadata."""

from __future__ import annotations

import subprocess
from collections import defaultdict

from wlrenv.niri import ipc, positions
from wlrenv.niri.identify import ProcessInfo, identify_mosh, identify_tmux
from wlrenv.niri.positions import PositionEntry


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

    # Collect entries per workspace for each app
    tmux_entries: dict[int, list[PositionEntry]] = defaultdict(list)
    mosh_entries: dict[int, list[PositionEntry]] = defaultdict(list)

    for window in windows:
        ws = workspaces.get(window.workspace_id)
        if not ws:
            continue
        output = outputs.get(ws.output)
        if not output:
            continue

        width_percent = calculate_width_percent(window.tile_width, output.width)

        children = get_child_processes(window.pid)
        for child in children:
            if identity := identify_tmux(child):
                if window.column is not None:
                    tmux_entries[window.workspace_id].append(
                        PositionEntry(
                            id=f"tmux:{identity}",
                            index=window.column,
                            window_id=window.id,
                            width=width_percent,
                        )
                    )
                break
            if identity := identify_mosh(child):
                if window.column is not None:
                    mosh_entries[window.workspace_id].append(
                        PositionEntry(
                            id=f"mosh:{identity}",
                            index=window.column,
                            window_id=window.id,
                            width=width_percent,
                        )
                    )
                break

    # Upsert entries for each app/workspace
    for workspace_id, entries in tmux_entries.items():
        positions.upsert_entries(app="tmux", workspace_id=workspace_id, entries=entries)

    for workspace_id, entries in mosh_entries.items():
        positions.upsert_entries(app="mosh", workspace_id=workspace_id, entries=entries)

    # Prune dominated boots
    boot_id = positions.get_boot_id()
    positions.prune_dominated_boots(boot_id)
