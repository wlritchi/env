"""Restore terminal windows to their saved workspaces."""

from __future__ import annotations

import os
import subprocess

from wlrenv.niri import ipc
from wlrenv.niri.storage import lookup


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


def restore_tmux() -> None:
    """Restore detached tmux sessions to their saved workspaces."""
    sessions = get_detached_tmux_sessions()

    for session in sessions:
        # Skip IDE embedded terminal sessions (7-char hex hash of IDE + project dir)
        if len(session) == 7 and all(c in "0123456789abcdef" for c in session):
            continue

        props = lookup("tmux", session)
        proc = spawn_terminal(["tmux", "attach-session", "-t", session])

        if props:
            window_id = ipc.wait_for_window(pid=proc.pid)
            if window_id:
                ipc.configure(
                    window_id, workspace=props["workspace"], width=props["width"]
                )


def restore_mosh() -> None:
    """Restore mosh sessions to their saved workspaces.

    Spawns terminals that prompt before connecting, allowing the user
    to handle interactive authentication (e.g., SSH key passphrase) at
    their convenience.
    """
    sessions = read_moshen_sessions()

    for host, session_name in sessions:
        identity = f"{host}:{session_name}"
        props = lookup("mosh", identity)
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
            window_id = ipc.wait_for_window(pid=proc.pid)
            if window_id:
                ipc.configure(
                    window_id, workspace=props["workspace"], width=props["width"]
                )
