# src/wlrenv/niri/identify.py
"""Identify terminal sessions from child processes."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class ProcessInfo:
    """Information about a process."""

    comm: str
    args: list[str]


def identify_tmux(proc: ProcessInfo) -> str | None:
    """Extract tmux session name from process info."""
    if not proc.comm.startswith("tmux"):
        return None

    # Look for -t <session> in args
    args_str = " ".join(proc.args)
    match = re.search(r"-t\s+([^\s]+)", args_str)
    if match:
        return match.group(1)

    return None


def identify_mosh(proc: ProcessInfo) -> str | None:
    """Extract host:session from moshen process info."""
    if proc.comm != "moshen":
        return None

    # moshen <host> [session]
    if len(proc.args) >= 2:
        host = proc.args[1]
        session = proc.args[2] if len(proc.args) >= 3 else "main"
        return f"{host}:{session}"

    return None
