"""Restore terminal windows to their saved workspaces."""

from __future__ import annotations

import logging
import os
import subprocess

from wlrenv.niri import ipc, ordering, positions
from wlrenv.niri.positions import PositionEntry

logger = logging.getLogger(__name__)


def get_detached_tmux_sessions() -> list[str]:
    """Get list of detached tmux session names."""
    try:
        result = subprocess.run(  # noqa: S603
            [  # noqa: S607
                "tmux",
                "list-sessions",
                "-F",
                "#{session_name}",
                "-f",
                "#{?#{session_attached},0,1}",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return []
        return [s.strip() for s in result.stdout.strip().split("\n") if s.strip()]
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []


def read_moshen_sessions() -> list[tuple[str, str]]:
    """Read mosh sessions from moshen state file."""
    import base64
    from pathlib import Path

    try:
        state_dir = os.environ.get(
            "XDG_STATE_HOME", str(Path.home() / ".local" / "state")
        )
        sessions_file = Path(state_dir) / "moshen" / "sessions"

        if not sessions_file.exists():
            return []

        sessions = []
        for line in sessions_file.read_text().strip().split("\n"):
            if not line:
                continue
            parts = line.split(":")
            if len(parts) >= 2:
                host = base64.b64decode(parts[0]).decode().strip()
                session = base64.b64decode(parts[1]).decode().strip()
                sessions.append((host, session))

        return sessions
    except (OSError, ValueError, UnicodeDecodeError):
        # Return empty list on file read errors, base64 decode errors, or unicode errors
        return []


def spawn_terminal(args: list[str]) -> subprocess.Popen[bytes]:
    """Spawn a terminal with the given command."""
    terminal = os.environ.get("TERMINAL", "alacritty")
    return subprocess.Popen([terminal, "-e", *args])  # noqa: S603


def _get_window_column(window_id: int, workspace_id: int) -> int | None:
    """Get the current column of a window."""
    windows = ipc.get_windows()
    for w in windows:
        if w.id == window_id and w.workspace_id == workspace_id:
            logger.debug(
                "_get_window_column: window %d in workspace %d -> column %s",
                window_id,
                workspace_id,
                w.column,
            )
            return w.column
    logger.warning(
        "_get_window_column: window %d NOT FOUND in workspace %d",
        window_id,
        workspace_id,
    )
    return None


def restore_tmux() -> None:
    """Restore detached tmux sessions to their saved workspaces."""
    sessions = get_detached_tmux_sessions()
    restored_entries: dict[int, list[PositionEntry]] = {}

    for session in sessions:
        # Skip IDE embedded terminal sessions (7-char hex hash of IDE + project dir)
        if len(session) == 7 and all(c in "0123456789abcdef" for c in session):
            continue

        stable_id = f"tmux:{session}"
        props = positions.lookup_latest_position(stable_id)
        proc = spawn_terminal(["tmux", "attach-session", "-t", session])

        if props:
            logger.info(
                "restore_tmux: restoring %s to workspace %d width %d",
                stable_id,
                props.workspace_id,
                props.width,
            )
            window_id = ipc.wait_for_window(pid=proc.pid)
            if window_id:
                workspace_id = props.workspace_id
                logger.info(
                    "restore_tmux: [%s] got window_id=%d, configuring...",
                    stable_id,
                    window_id,
                )
                ipc.configure(window_id, workspace=workspace_id, width=props.width)

                # Place window in correct column order
                column = _get_window_column(window_id, workspace_id)
                logger.info(
                    "restore_tmux: [%s] after configure, column=%s",
                    stable_id,
                    column,
                )
                if column is not None:
                    ordering.place_window(
                        window_id=window_id,
                        identity=stable_id,
                        workspace_id=workspace_id,
                        current_column=column,
                    )
                    # Record entry for position storage
                    if workspace_id not in restored_entries:
                        restored_entries[workspace_id] = []
                    restored_entries[workspace_id].append(
                        PositionEntry(
                            id=stable_id,
                            index=column,
                            window_id=window_id,
                            width=props.width,
                        )
                    )
                else:
                    logger.warning(
                        "restore_tmux: [%s] column is None after configure, skipping placement!",
                        stable_id,
                    )

    # Record restored positions
    for workspace_id, entries in restored_entries.items():
        positions.upsert_entries("tmux", workspace_id, entries)

    if restored_entries:
        positions.prune_dominated_boots(positions.get_boot_id())


def restore_mosh() -> None:
    """Restore mosh sessions to their saved workspaces.

    Spawns terminals that prompt before connecting, allowing the user
    to handle interactive authentication (e.g., SSH key passphrase) at
    their convenience.
    """
    sessions = read_moshen_sessions()
    restored_entries: dict[int, list[PositionEntry]] = {}

    for host, session_name in sessions:
        identity = f"{host}:{session_name}"
        stable_id = f"mosh:{identity}"
        props = positions.lookup_latest_position(stable_id)
        # Prompt before connecting to allow interactive auth at user's convenience
        proc = spawn_terminal(
            [
                "bash",
                "-c",
                'read -p "Press enter to connect to $1" && exec moshen "$1" "$2"',
                "--",
                host,
                session_name,
            ]
        )

        if props:
            logger.info(
                "restore_mosh: restoring %s to workspace %d width %d",
                stable_id,
                props.workspace_id,
                props.width,
            )
            window_id = ipc.wait_for_window(pid=proc.pid)
            if window_id:
                workspace_id = props.workspace_id
                logger.info(
                    "restore_mosh: [%s] got window_id=%d, configuring...",
                    stable_id,
                    window_id,
                )
                ipc.configure(window_id, workspace=workspace_id, width=props.width)

                # Place window in correct column order
                column = _get_window_column(window_id, workspace_id)
                logger.info(
                    "restore_mosh: [%s] after configure, column=%s",
                    stable_id,
                    column,
                )
                if column is not None:
                    ordering.place_window(
                        window_id=window_id,
                        identity=stable_id,
                        workspace_id=workspace_id,
                        current_column=column,
                    )
                    # Record entry for position storage
                    if workspace_id not in restored_entries:
                        restored_entries[workspace_id] = []
                    restored_entries[workspace_id].append(
                        PositionEntry(
                            id=stable_id,
                            index=column,
                            window_id=window_id,
                            width=props.width,
                        )
                    )
                else:
                    logger.warning(
                        "restore_mosh: [%s] column is None after configure, skipping placement!",
                        stable_id,
                    )

    # Record restored positions
    for workspace_id, entries in restored_entries.items():
        positions.upsert_entries("mosh", workspace_id, entries)

    if restored_entries:
        positions.prune_dominated_boots(positions.get_boot_id())
