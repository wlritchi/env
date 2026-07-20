"""Integration: parse/rebuild the Bun blob of a real installed Claude binary.

Skipped unless a binary is found (``CCPATCH_TEST_BINARY`` or a local Bun
install). Does not execute the Claude binary itself: it validates that the
container + blob round-trips structurally on real data, and that the injected
compact_session tool is runtime shape-complete against the real cli.js (the
latter node-evals the extracted tool object, and skips without node/bun).
"""

from __future__ import annotations

import glob
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from wlrenv.ccpatch.bunfmt import parse_blob, rebuild_blob
from wlrenv.ccpatch.container import load_container
from wlrenv.ccpatch.patches import COMPACT_SESSION, PatchError


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


# --- runtime shape-completeness of the injected compact_session tool ----------
#
# The static catalog test (test_compact_session_tool_shape_complete) pins a member
# list. This integration test instead derives the required members MECHANICALLY from
# the real cli.js and node-evals the actually-injected tool object, so it self-updates
# when a future CC build direct-calls a new method on tools -- the exact failure mode
# that shipped three runtime bugs (missing prompt/renderToolUseMessage, wrong result
# shape) past the static apply()+verify checks.

# Members the aK tool constructor / base object provide by default; the injected tool
# need not define these.
_AK_DEFAULT_MEMBERS = frozenset(
    {
        "isEnabled",
        "isConcurrencySafe",
        "isReadOnly",
        "isDestructive",
        "checkPermissions",
        "toAutoClassifierInput",
        "userFacingName",
    }
)
# The invocation-contract methods the harness calls on every tool but that are too
# noisy to derive mechanically (`.call(` alone is Function.prototype.call, 1000+ hits).
# The volatile render/dispatch layer IS derived, from the unambiguous `.tool.M(`.
_PROTOCOL_CORE_MEMBERS = frozenset(
    {
        "description",
        "inputSchema",
        "prompt",
        "call",
        "mapToolResultToToolResultBlockParam",
    }
)

_EVAL_TEMPLATE = """\
globalThis.%(builder)s=(o)=>Object.defineProperties(\
{isEnabled:()=>!0,isConcurrencySafe:()=>!1,isReadOnly:()=>!1,isDestructive:()=>!1,\
checkPermissions:()=>({}),toAutoClassifierInput:()=>"",userFacingName:()=>""},\
Object.getOwnPropertyDescriptors(o));
globalThis.%(ns)s={object:()=>({}),strictObject:()=>({})};
%(tool)s;
const t=globalThis.__ccCompactTool,req=%(req)s;
console.log(JSON.stringify(req.filter((m)=>t[m]===undefined)));
"""


def _entry_source(binary: bytes) -> str:
    blob = parse_blob(load_container(binary).read_blob())
    entry = next(m for m in blob.modules if m.is_entrypoint())
    data = entry.contents
    if isinstance(data, (bytes, bytearray)):
        return data.decode("utf-8", "surrogatepass")
    return data


def _required_tool_members(source: str) -> list[str]:
    # Methods the generic renderer/dispatcher calls UNCONDITIONALLY on a tool object,
    # scraped from `X.tool.M(` (dropping optional-chained `.tool.M?.(`), unioned with
    # the pinned invocation core, minus what the constructor already supplies.
    accessed = set(re.findall(r"(?<!\?)\.tool\.([A-Za-z_$][\w$]*)\(", source))
    accessed -= {m for m in accessed if f".tool.{m}?.(" in source}
    return sorted((accessed | _PROTOCOL_CORE_MEMBERS) - _AK_DEFAULT_MEMBERS)


def _slice_injected_tool(patched: str) -> str:
    # The injected `globalThis.__ccCompactTool=<builder>({...})` assignment, sliced by
    # a balanced-paren scan (the tool object contains no unbalanced parens in strings).
    start = patched.index("globalThis.__ccCompactTool=")
    depth = 0
    for i in range(patched.index("(", start), len(patched)):
        if patched[i] == "(":
            depth += 1
        elif patched[i] == ")":
            depth -= 1
            if depth == 0:
                return patched[start : i + 1]
    raise AssertionError("unbalanced injected compact_session tool object")


def test_compact_session_tool_runtime_shape_complete(binary_bytes: bytes) -> None:
    runtime = shutil.which("node") or shutil.which("bun")
    if runtime is None:
        pytest.skip("no node/bun to eval the injected tool object")
    source = _entry_source(binary_bytes)
    try:
        patched = COMPACT_SESSION.apply(source)
    except PatchError as exc:
        pytest.skip(f"compact_session patch does not apply to this binary: {exc}")
    required = _required_tool_members(patched)
    tool = _slice_injected_tool(patched)
    m_builder = re.search(r"globalThis\.__ccCompactTool=([\w$]+)\(", tool)
    m_ns = re.search(r"inputSchema\(\)\{return ([\w$]+)\.object", tool)
    assert m_builder is not None and m_ns is not None
    js = _EVAL_TEMPLATE % {
        "builder": m_builder.group(1),
        "ns": m_ns.group(1),
        "tool": tool,
        "req": json.dumps(required),
    }
    proc = subprocess.run(  # noqa: S603 - runtime is which()-resolved node/bun
        [runtime, "-e", js], capture_output=True, text=True, timeout=30
    )
    assert proc.returncode == 0, f"{runtime} eval failed: {proc.stderr.strip()}"
    missing = json.loads(proc.stdout.strip())
    assert not missing, (
        f"injected compact_session tool missing harness-called members: {missing}"
    )
