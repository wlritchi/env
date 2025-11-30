# tests/niri/test_identify.py
from __future__ import annotations

from dataclasses import dataclass

from wlrenv.niri.identify import ProcessInfo, identify_mosh, identify_tmux


@dataclass
class MockProc:
    comm: str
    args: list[str]


def test_identify_tmux_with_client() -> None:
    proc = ProcessInfo(
        comm="tmux: client", args=["tmux", "attach-session", "-t", "main"]
    )
    result = identify_tmux(proc)
    assert result == "main"


def test_identify_tmux_with_bare_tmux() -> None:
    proc = ProcessInfo(comm="tmux", args=["tmux", "attach-session", "-t", "work"])
    result = identify_tmux(proc)
    assert result == "work"


def test_identify_tmux_without_session() -> None:
    proc = ProcessInfo(comm="tmux", args=["tmux", "new-session"])
    result = identify_tmux(proc)
    assert result is None


def test_identify_tmux_wrong_process() -> None:
    proc = ProcessInfo(comm="bash", args=["bash"])
    result = identify_tmux(proc)
    assert result is None


def test_identify_mosh_with_session() -> None:
    proc = ProcessInfo(comm="moshen", args=["moshen", "server.example.com", "main"])
    result = identify_mosh(proc)
    assert result == "server.example.com:main"


def test_identify_mosh_default_session() -> None:
    proc = ProcessInfo(comm="moshen", args=["moshen", "server.example.com"])
    result = identify_mosh(proc)
    assert result == "server.example.com:main"


def test_identify_mosh_with_bash_interpreter() -> None:
    """Real-world case: bash scripts show /bin/bash in args."""
    proc = ProcessInfo(
        comm="moshen",
        args=["/bin/bash", "/home/user/.wlrenv/bin/ssh/moshen", "boron", "main"],
    )
    result = identify_mosh(proc)
    assert result == "boron:main"


def test_identify_mosh_with_bash_interpreter_default_session() -> None:
    """Real-world case: bash scripts show /bin/bash in args, default session."""
    proc = ProcessInfo(
        comm="moshen",
        args=["/bin/bash", "/home/user/.wlrenv/bin/ssh/moshen", "loop"],
    )
    result = identify_mosh(proc)
    assert result == "loop:main"


def test_identify_mosh_wrong_process() -> None:
    proc = ProcessInfo(comm="mosh-client", args=["mosh-client", "..."])
    result = identify_mosh(proc)
    assert result is None
