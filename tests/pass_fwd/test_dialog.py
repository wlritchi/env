import asyncio
import sys
from pathlib import Path

import pytest

from wlrenv.pass_fwd.dialog import (
    DecryptRequest,
    Pinentry,
    SignRequest,
    decrypt_prompt,
    escape,
    sign_prompt,
)
from wlrenv.pass_fwd.index import Lookup

FAKE_PINENTRY = """\
import sys, time
log = open(sys.argv[1], "a")
print("OK hello", flush=True)
for line in sys.stdin:
    log.write(line)
    log.flush()
    if line.startswith(("CONFIRM", "MESSAGE")):
        if "{mode}" == "hang":
            time.sleep(30)
        print("OK" if "{mode}" == "ok" else "ERR 83886179 Operation cancelled", flush=True)
    else:
        print("OK", flush=True)
"""


def fake_pinentry(tmp_path: Path, mode: str) -> tuple[str, Path]:
    script = tmp_path / "pinentry.py"
    script.write_text(FAKE_PINENTRY.replace("{mode}", mode))
    log = tmp_path / "log"
    wrapper = tmp_path / "pinentry"
    wrapper.write_text(f"#!/bin/sh\nexec {sys.executable} {script} {log}\n")
    wrapper.chmod(0o755)
    return str(wrapper), log


REQUEST = DecryptRequest("neon", Lookup(current=("Finance/bank",)))


@pytest.mark.parametrize(("mode", "expected"), [("ok", True), ("cancel", False)])
def test_confirm(tmp_path: Path, mode: str, expected: bool) -> None:
    program, log = fake_pinentry(tmp_path, mode)
    assert asyncio.run(Pinentry(program).confirm(REQUEST)) is expected
    sent = log.read_text()
    assert "SETDESC neon wants to decrypt:%0A%0A    Finance/bank" in sent
    assert "SETOK Approve" in sent
    assert sent.splitlines()[-1] == "CONFIRM"


def test_timeout_denies(tmp_path: Path) -> None:
    program, _log = fake_pinentry(tmp_path, "hang")
    assert asyncio.run(Pinentry(program, timeout=0.5).confirm(REQUEST)) is False


def test_missing_program_denies(tmp_path: Path) -> None:
    assert asyncio.run(Pinentry(str(tmp_path / "missing")).confirm(REQUEST)) is False


def test_escape() -> None:
    assert escape("50%\r\nx") == b"50%25%0D%0Ax"


class TestPrompts:
    def test_copy_warning(self) -> None:
        text = decrypt_prompt(
            DecryptRequest("neon", Lookup(current=("Finance/bank", "Social/x")))
        ).description
        assert "WARNING" in text
        assert "    Finance/bank\n    Social/x" in text

    def test_old_version(self) -> None:
        text = decrypt_prompt(
            DecryptRequest("neon", Lookup(earlier=("Finance/bank",)))
        ).description
        assert "old version" in text

    def test_sign_warns_and_shows_digest(self) -> None:
        prompt = sign_prompt(
            SignRequest("neon", "E6542F8D51DCCF82", "SHA-512", "AB" * 32)
        )
        assert "CANNOT SEE WHAT WILL BE SIGNED" in prompt.description
        assert "E654 2F8D 51DC CF82" in prompt.description
        assert "SHA-512: ABAB ABAB" in prompt.description
        assert prompt.ok == "Sign"
