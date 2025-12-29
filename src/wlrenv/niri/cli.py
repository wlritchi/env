# src/wlrenv/niri/cli.py
"""CLI entry points for niri window tracking."""

from __future__ import annotations

import logging
import os
import sys

from wlrenv.niri.ipc import NiriError
from wlrenv.niri.librewolf_host import main as librewolf_host_main
from wlrenv.niri.restore import restore_mosh, restore_tmux
from wlrenv.niri.track import track_terminals


def _setup_logging() -> None:
    """Configure logging based on NIRI_DEBUG environment variable."""
    level_str = os.environ.get("NIRI_DEBUG", "").upper()
    if level_str in ("1", "TRUE", "INFO"):
        level = logging.INFO
    elif level_str == "DEBUG":
        level = logging.DEBUG
    else:
        return  # No logging setup if not enabled

    logging.basicConfig(
        level=level,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def track_terminals_cli() -> None:
    """CLI entry point for terminal tracking."""
    _setup_logging()
    try:
        track_terminals()
    except NiriError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def restore_tmux_cli() -> None:
    """CLI entry point for tmux restoration."""
    _setup_logging()
    try:
        restore_tmux()
    except NiriError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def restore_mosh_cli() -> None:
    """CLI entry point for mosh restoration."""
    _setup_logging()
    try:
        restore_mosh()
    except NiriError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def librewolf_native_host_cli() -> None:
    """CLI entry point for Librewolf native messaging host."""
    librewolf_host_main()
