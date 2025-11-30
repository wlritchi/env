# src/wlrenv/niri/cli.py
"""CLI entry points for niri window tracking."""

from __future__ import annotations

import sys

from wlrenv.niri.ipc import NiriError
from wlrenv.niri.restore import restore_mosh, restore_tmux
from wlrenv.niri.track import track_terminals


def track_terminals_cli() -> None:
    """CLI entry point for terminal tracking."""
    try:
        track_terminals()
    except NiriError as e:
        print(f"Error: {e}", file=sys.stderr)  # noqa: T201
        sys.exit(1)


def restore_tmux_cli() -> None:
    """CLI entry point for tmux restoration."""
    try:
        restore_tmux()
    except NiriError as e:
        print(f"Error: {e}", file=sys.stderr)  # noqa: T201
        sys.exit(1)


def restore_mosh_cli() -> None:
    """CLI entry point for mosh restoration."""
    try:
        restore_mosh()
    except NiriError as e:
        print(f"Error: {e}", file=sys.stderr)  # noqa: T201
        sys.exit(1)
