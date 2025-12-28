"""Track terminal windows and store their metadata."""

from __future__ import annotations

import os
import re
import subprocess
from collections import defaultdict

from wlrenv.niri import ipc, positions
from wlrenv.niri.identify import ProcessInfo, identify_mosh
from wlrenv.niri.positions import PositionEntry


def get_tmux_client_sessions() -> dict[str, str]:
    """Get mapping of tty path to session name from tmux.

    Returns a dict like {"/dev/pts/146": "secwrap", ...}.
    This queries tmux once for all attached clients.
    """
    try:
        result = subprocess.run(  # noqa: S603
            ["tmux", "list-clients"],  # noqa: S607
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return {}
    except (subprocess.CalledProcessError, FileNotFoundError):
        return {}

    # Parse lines like: /dev/pts/146: secwrap [152x99 alacritty] (attached,UTF-8)
    sessions: dict[str, str] = {}
    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        match = re.match(r"(/dev/[^:]+):\s+(\S+)", line)
        if match:
            tty_path, session_name = match.groups()
            sessions[tty_path] = session_name
    return sessions


def get_process_tty(pid: int) -> str | None:
    """Get the tty path for a process by reading /proc/<pid>/fd/0.

    Returns something like "/dev/pts/146" or None if not available.
    """
    try:
        return os.readlink(f"/proc/{pid}/fd/0")
    except (OSError, FileNotFoundError):
        return None


def calculate_width_percent(tile_width: float, output_width: int) -> int:
    """Calculate width as percentage of output, rounded to nearest 10%."""
    pct = tile_width / output_width * 100
    return int((pct + 5) // 10 * 10)


def get_child_processes(pid: int) -> list[tuple[int, ProcessInfo]]:
    """Get child processes of given PID.

    Returns list of (child_pid, ProcessInfo) tuples.
    """
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
                children.append((cpid, ProcessInfo(comm=comm, args=args)))
        except subprocess.CalledProcessError:
            continue

    return children


def track_terminals() -> None:
    """Track all terminal windows and store their workspace/width."""
    windows = ipc.get_windows(app_id="Alacritty")
    outputs = {o.name: o for o in ipc.get_outputs()}
    workspaces = {w.id: w for w in ipc.get_workspaces()}

    # Get tmux session mapping once for all windows
    tmux_sessions = get_tmux_client_sessions()

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
        for child_pid, child in children:
            # Check for tmux client by looking up its tty in the session mapping
            if child.comm.startswith("tmux"):
                tty = get_process_tty(child_pid)
                if tty and tty in tmux_sessions:
                    session_name = tmux_sessions[tty]
                    if window.column is not None:
                        tmux_entries[window.workspace_id].append(
                            PositionEntry(
                                id=f"tmux:{session_name}",
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
