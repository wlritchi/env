"""Integration: parse/rebuild the Bun blob of a real installed Claude binary.

Skipped unless a binary is found (``CCPATCH_TEST_BINARY`` or a local Bun
install). Does not execute the binary; it only validates that the container +
blob round-trips structurally on real data.
"""

from __future__ import annotations

import glob
import os
from pathlib import Path

import pytest

from wlrenv.ccpatch.bunfmt import parse_blob, rebuild_blob
from wlrenv.ccpatch.container import load_container


def _find_binary() -> Path | None:
    env = os.environ.get("CCPATCH_TEST_BINARY")
    if env and Path(env).is_file():
        return Path(env)
    pattern = os.path.expanduser(
        "~/.claude-bun_*/install/global/node_modules/@anthropic-ai/claude-code-*/claude"
    )
    matches = sorted(glob.glob(pattern))
    return Path(matches[-1]) if matches else None


@pytest.fixture(scope="module")
def binary_bytes() -> bytes:
    path = _find_binary()
    if path is None:
        pytest.skip("no Claude Bun binary found (set CCPATCH_TEST_BINARY)")
    assert path is not None  # narrow type: pytest.skip above does not return
    return path.read_bytes()


def test_real_blob_round_trips(binary_bytes: bytes) -> None:
    container = load_container(binary_bytes)
    blob = parse_blob(container.read_blob())
    assert parse_blob(rebuild_blob(blob)) == blob


def test_real_binary_has_entrypoint_source(binary_bytes: bytes) -> None:
    blob = parse_blob(load_container(binary_bytes).read_blob())
    entry = [m for m in blob.modules if m.is_entrypoint()]
    assert len(entry) == 1
    assert len(entry[0].contents) > 1_000_000  # multi-MB minified cli.js


def test_write_blob_is_loadable_again(binary_bytes: bytes) -> None:
    container = load_container(binary_bytes)
    blob = parse_blob(container.read_blob())
    new_file = container.write_blob(rebuild_blob(blob))
    # The rewritten file must re-parse as a valid container + blob.
    blob2 = parse_blob(load_container(new_file).read_blob())
    assert blob2 == blob
