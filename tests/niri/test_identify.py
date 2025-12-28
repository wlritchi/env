# tests/niri/test_identify.py
from __future__ import annotations

from wlrenv.niri.identify import ProcessInfo, identify_mosh


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
