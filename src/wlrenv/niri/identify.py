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

    # Find the moshen script in args (could be args[0] or args[1] if run via bash)
    # Then host and session follow it
    moshen_idx = None
    for i, arg in enumerate(proc.args):
        if arg == "moshen" or arg.endswith("/moshen"):
            moshen_idx = i
            break

    if moshen_idx is None:
        return None

    # moshen <host> [session]
    host_idx = moshen_idx + 1
    session_idx = moshen_idx + 2

    if len(proc.args) > host_idx:
        host = proc.args[host_idx]
        session = proc.args[session_idx] if len(proc.args) > session_idx else "main"
        return f"{host}:{session}"

    return None
