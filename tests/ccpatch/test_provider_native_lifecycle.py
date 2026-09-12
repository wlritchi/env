"""Exercise the complete captured .206 worker, not a replacement worker class."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from wlrenv.ccpatch.patches import (
    _PROVIDER_ENV_SNAPSHOT,
    BACKGROUND_PROVIDER_ENV_198,
    _provider_key_sources,
)

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def native_lifecycle_source() -> dict[str, str]:
    path = ROOT / "build/sweep-resume/2.1.206/linux-x64/original.js"
    if not path.is_file():
        pytest.skip("requires captured pristine .206 linux-x64 source")
    original = path.read_text()
    patched = BACKGROUND_PROVIDER_ENV_198.apply(original)
    start = patched.index("class Bce{")
    end = patched.index("var _sn,Gua,lJ,hMo,", start)
    worker = patched[start:end]
    assert "static async adopt(" in worker
    assert "static spawn(" in worker
    assert "onExit(e,t){" in worker
    assert worker.endswith("}")
    snapshot = _PROVIDER_ENV_SNAPSHOT.search(original)
    assert snapshot is not None
    patch = next(
        item
        for item in BACKGROUND_PROVIDER_ENV_198.patches
        if item.name == "snapshot-transient-provider-env"
    )
    assert not isinstance(patch.replacement, str)
    helpers = patch.replacement(snapshot)
    registry = "".join(
        f'const {name}=["ANTHROPIC_API_KEY","ANTHROPIC_AUTH_TOKEN","ANTHROPIC_BASE_URL"];'
        for name in _provider_key_sources(original)
    )
    # Use the native orphan sweeps and manager adoption block without edits.
    orphan_start = patched.index("async function rOa(")
    orphan_end = patched.index("function z1b(", orphan_start)
    manager_start = patched.index("async function nNb(")
    manager_end = patched.index("var Lf,y0f,dOa,", manager_start)
    adoption_start = patched.index("await Promise.all(Object.entries(A.workers)")
    adoption_end = patched.index(")", patched.index("})),T+x+I", adoption_start))
    adoption = patched[adoption_start : adoption_end + 2]
    return {
        "stall": patched[
            patched.index("function bx_(") : patched.index("async function fbp(")
        ],
        "worker": worker,
        "client": patched[patched.index("function qRp(") : patched.index("var WRp,")],
        "lines": patched[patched.index("function aRo(") : patched.index("var w_p,")],
        "environment": patched[
            patched.index("function eLp(") : patched.index("async function Vua(")
        ],
        "helpers": registry + helpers,
        "sweeps": patched[orphan_start:orphan_end] + patched[manager_start:manager_end],
        "adoption": adoption,
        "auth": patched[patched.index("function QTe(") : patched.index("var Yxd,")],
        "endpoint": patched[
            patched.index("async function Jxy(") : patched.index("async function nkd(")
        ],
        "server": patched[
            patched.index("function Zxy(") : patched.index("async function nkd(")
        ],
    }


@pytest.mark.parametrize(
    "scenario",
    [
        "replaced-socket",
        "stall-unresolved",
        "stall-recovered",
        "constructor",
        "cold",
        "adopt-map-sweep",
        "dead",
        "identity-mismatch",
        "pending-upgrade",
        "rekey",
        "stale",
        "retire",
        "crash",
        "handoff",
        "wrong-mac",
        "wrong-proto",
        "wrong-version",
        "wrong-session",
        "wrong-nonce",
        "partial",
        "timeout",
        "replay",
        "reconnect",
        "crash-response",
        "missing-auth",
        "wrong-auth",
        "unauthenticated",
        "unsupported",
    ],
)
def test_native_provider_lifecycle(
    native_lifecycle_source: dict[str, str], scenario: str, tmp_path: Path
) -> None:
    runtime = shutil.which("node")
    if runtime is None:
        pytest.skip("requires node")
    fixture = tmp_path / "native.json"
    fixture.write_text(json.dumps(native_lifecycle_source))
    result = subprocess.run(  # noqa: S603 - Run the extracted native code in a mock context.
        [
            runtime,
            str(Path(__file__).with_name("provider_native_lifecycle.mjs")),
            str(fixture),
            scenario,
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(
    os.environ.get("CCPATCH_PROCESS_INTEGRATION") != "1",
    reason="opt in with CCPATCH_PROCESS_INTEGRATION=1; extracted native Unix transport",
)
@pytest.mark.parametrize("scenario", ["handoff", "wrong-auth"])
def test_native_provider_process_takeover(
    native_lifecycle_source: dict[str, str], scenario: str
) -> None:
    """Run native transport in child processes, not the full CLI or PTY host.

    CLI startup also initializes telemetry, auth, and other integrations. This
    bounded harness does not run that startup path. No provider request is needed
    to test the memory-only snapshot protocol. All credentials are synthetic.
    """
    runtime = shutil.which("node")
    if runtime is None or sys.platform != "linux":
        pytest.skip("requires node, Linux /proc, and Unix sockets")
    # Keep socket paths below the Unix socket length limit.
    with tempfile.TemporaryDirectory(prefix="ccp-") as directory:
        scratch = Path(directory)
        fixture = scratch / "native.json"
        fixture.write_text(json.dumps(native_lifecycle_source))
        env = {"PATH": str(Path(runtime).parent), "LANG": "C.UTF-8"}
        for key in (
            "HOME",
            "CLAUDE_CONFIG_DIR",
            "XDG_CONFIG_HOME",
            "XDG_STATE_HOME",
            "XDG_CACHE_HOME",
            "XDG_RUNTIME_DIR",
            "TMPDIR",
        ):
            target = scratch / key.lower()
            target.mkdir(mode=0o700)
            env[key] = str(target)
        with subprocess.Popen(  # noqa: S603 - Isolated local test processes only.
            [
                runtime,
                str(Path(__file__).with_name("provider_process_lifecycle.mjs")),
                str(fixture),
                scenario,
            ],
            cwd=scratch,
            env=env,
            start_new_session=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ) as process:
            try:
                stdout, stderr = process.communicate(timeout=30)
                assert process.returncode == 0, stdout + stderr
            finally:
                # Kill the test process group even if the controller has exited.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=5)
