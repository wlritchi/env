"""Integration: parse/rebuild the Bun blob of a real installed Claude binary.

Skipped unless a binary is found (``CCPATCH_TEST_BINARY`` or the Nix profile).
Does not execute the Claude binary itself: it validates that the
container + blob round-trips structurally on real data, and that the injected
compact_session tool is runtime shape-complete against the real cli.js (the
latter node-evals the extracted tool object, and skips without node/bun).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from wlrenv.ccpatch.bunfmt import parse_blob, rebuild_blob
from wlrenv.ccpatch.container import load_container
from wlrenv.ccpatch.patches import (
    _PROVIDER_ENV_EXPLICIT_KEYS,
    BACKGROUND_PROVIDER_ENV,
    COMPACT_SESSION,
    MULTI_PROVIDER_SDK,
    PatchError,
)


def test_unified_claude_uses_proxy_aware_launcher() -> None:
    root = Path(__file__).parents[2]
    launcher = (root / "machines/pkgs/claude-code.nix").read_text()

    assert "cc_openai_proxy_configure optional claude" in launcher
    assert "CC_OPENAI_PROXY_URL+x" in launcher
    assert "exit 1" in launcher


def _find_binary() -> tuple[Path | None, bool]:
    env = os.environ.get("CCPATCH_TEST_BINARY")
    if env and Path(env).is_file():
        return Path(env), True

    launcher = Path.home() / ".nix-profile/bin/claude"
    if launcher.is_file():
        binary = launcher.resolve().parent.parent / "libexec/claude-code/claude"
        if binary.is_file():
            return binary, False

    return None, False


@pytest.fixture(scope="module")
def binary_info() -> tuple[bytes, bool]:
    path, explicit = _find_binary()
    if path is None:
        pytest.skip("no Claude Bun binary found (set CCPATCH_TEST_BINARY)")
    assert path is not None  # narrow type: pytest.skip above does not return
    return path.read_bytes(), explicit


@pytest.fixture(scope="module")
def binary_bytes(binary_info: tuple[bytes, bool]) -> bytes:
    return binary_info[0]


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


def _provider_sources(binary_info: tuple[bytes, bool]) -> tuple[str, str]:
    binary_bytes, explicit = binary_info
    source = _entry_source(binary_bytes)
    if 'VERSION:"2.1.174"' not in source:
        if explicit:
            pytest.fail("CCPATCH_TEST_BINARY must be pristine Claude Code 2.1.174")
        pytest.skip("installed binary is not pristine Claude Code 2.1.174")
    if re.search(r"providerEnvVersion:\d+,providerEnv:AW9\(\)", source):
        if explicit:
            pytest.fail("CCPATCH_TEST_BINARY must be unpatched Claude Code 2.1.174")
        pytest.skip("installed Claude Code 2.1.174 binary is already patched")
    return source, BACKGROUND_PROVIDER_ENV.apply(source)


def test_patched_binary_help_initializes_on_opt_in_host() -> None:
    configured = os.environ.get("CCPATCH_TEST_PATCHED_BINARY")
    if configured is None:
        pytest.skip("set CCPATCH_TEST_PATCHED_BINARY for the host-side help test")
    path = Path(configured)
    if not path.is_file():
        pytest.fail("CCPATCH_TEST_PATCHED_BINARY must name a patched binary")
    source = _entry_source(path.read_bytes())
    if 'VERSION:"2.1.174"' not in source or "providerEnvVersion:3" not in source:
        pytest.fail(
            "CCPATCH_TEST_PATCHED_BINARY must be fully patched Claude Code 2.1.174"
        )

    try:
        proc = subprocess.run(  # noqa: S603 - explicit opt-in test binary
            [str(path), "--help"],
            capture_output=True,
            text=True,
            timeout=60,
            env={**os.environ, "DISABLE_AUTOUPDATER": "1"},
        )
    except subprocess.TimeoutExpired as exc:
        pytest.fail(f"patched binary --help timed out: {exc}")
    except OSError as exc:
        if exc.errno in {8, 13}:
            pytest.skip(f"patched binary loader is unsupported on this host: {exc}")
        raise

    assert proc.returncode == 0, proc.stderr
    assert "Usage:" in proc.stdout


def test_real_source_secures_background_provider_environment(
    binary_info: tuple[bytes, bool],
) -> None:
    source, patched = _provider_sources(binary_info)
    vertex_region_keys = set(re.findall(r"VERTEX_REGION_CLAUDE_[A-Z0-9_]+", source))
    assert vertex_region_keys == {
        key for key in _PROVIDER_ENV_EXPLICIT_KEYS if key.startswith("VERTEX_REGION_")
    }
    assert '"CLAUDE_CODE_CERT_STORE"' in patched
    assert patched.count("providerEnvVersion:3,providerEnv:AW9()") == 2
    assert "providerEnvVersion:3,short:" in patched
    assert patched.count(".providerEnvVersion!==3") == 3
    assert patched.count('code==="EPROVIDERENV"') == 2
    assert 'throw Object.assign(Error(' in patched
    for key in _PROVIDER_ENV_EXPLICIT_KEYS:
        assert f'"{key}"' in patched
    assert "W0q(H,$,q,K,_ccProviderEnv)" in patched
    assert "UVA(H,f,_.socketAuth(),$.claimAuth,_ccProviderEnv)" in patched
    assert "UVA(H,$,q,K,_ccProviderEnv)" in patched
    assert "wd.buildClaimFrame(H,$,q,_ccProviderEnv)" in patched
    assert "D(E,I+1,R,_ccProviderEnv)" in patched
    assert "D(I.dispatch,0,!0)" in patched
    assert "Ea9((E)=>void D(E)" in patched
    assert "dispatch:(E)=>void D(E)" in patched
    manager_start = patched.index("D=async(E,I=0,R,_ccProviderEnv)=>{")
    assert (
        patched[manager_start : manager_start + 180].count(
            "_ccProviderEnv=_ccProviderRetain(_ccProviderEnv);"
        )
        == 1
    )
    assert "for(let _ccKey of _ccProviderKeys())delete process.env[_ccKey]" in patched
    assert "providerEnv:k.record(k.enum(_ccProviderKeys())" in patched
    assert "K?.providerEnv??AW9()" not in patched
    assert "providerEnv:AW9(),sessionPermissionRules" not in patched
    assert "providerEnv:v?.providerEnv" not in patched
    assert "providerEnv:H.providerEnv" not in patched
    assert "...K.providerEnv&&{providerEnv:K.providerEnv}" not in patched
    file_fallback = patched[patched.index("let D=IH({...H,nonce:O})") :]
    assert (
        "providerEnv" not in file_fallback[: file_fallback.index("await jO(A,D,384)")]
    )
    assert "Restart the stale Claude Code daemon and try again" in patched

    builder_start = patched.index("function I09(")
    builder = patched[
        builder_start : patched.index("async function pXq", builder_start)
    ]
    payload = "let _ccProviderPayload=_ccProviderRetain(_ccProviderEnv)"
    initial_apply = "Object.entries(_ccProviderPayload)"
    native_scrubs = ("for(let z of UXq)", "for(let z of FXq)", "WG$.some")
    final_apply = "Object.entries(_ccProviderPayload)"
    assert builder.index(payload) < builder.index(initial_apply)
    for native_scrub in native_scrubs:
        assert builder.index(initial_apply) < builder.index(native_scrub)
        assert builder.index(native_scrub) < builder.rindex(final_apply)
    assert builder.rindex(final_apply) < builder.index("return A}")
    assert "_ccProviderSnapshotFromEnv" not in builder
    assert (
        "A.CLAUDE_CODE_PROVIDER_ENV_TRANSIENT=JSON.stringify(_ccProviderPayload)"
        in builder
    )

    capture = "_ccProviderWorkerEnv=_ccProviderCaptureTransport("
    capture_start = patched.index(capture, patched.index("async function O09"))
    claimed_start = patched.rfind("async function ", 0, capture_start)
    claimed = patched[claimed_start : capture_start + 2000]
    assign = claimed.index("Object.assign(process.env,", claimed.index(capture))
    final_apply = claimed.index("_ccProviderApplyWorkerFinal()", assign)
    assert claimed.index(capture) < assign < final_apply
    assert final_apply < claimed.index("await ", final_apply)
    assert "_ccProviderSnapshotFromEnv" not in patched

    delayed_start = patched.index("Vy$().then(async()=>")
    delayed = patched[delayed_start : delayed_start + 500]
    assert (
        delayed.index("Ko()")
        < delayed.index("_ccProviderApplyWorkerFinal()")
        < delayed.index("await vLq()")
    )
    operational_start = patched.index("Ko(),CB$(_ccProviderWorkerEnv)")
    operational = patched[operational_start : operational_start + 250]
    assert operational.index("CB$(_ccProviderWorkerEnv)") < operational.index(
        "_ccProviderApplyWorkerFinal()"
    )


def test_real_source_routes_multi_provider_sdk(
    binary_info: tuple[bytes, bool],
) -> None:
    _, provider_patched = _provider_sources(binary_info)
    patched = MULTI_PROVIDER_SDK.apply(provider_patched)

    assert "const _ccMultiProviderSDK=()=>GC" in patched
    assert patched.count("_ccMultiProviderRoute(") == 5
    assert patched.count("let _ccRequest=") >= 2
    assert "_ccMultiProviderCatalog.find" in patched
    for model in (
        "kimi:kimi-k3",
        "kimi:kimi-k2.7-code",
        "zai:glm-5.3",
        "zai:glm-5.3-flash",
        "zai:glm-5.2",
        "zai:glm-5-turbo",
        "zai:glm-4.7",
        "zai:glm-4.5-air",
        "minimax:MiniMax-M3",
        "minimax:MiniMax-M2.7",
        "openai:gpt-6-astra",
        "openai:gpt-5.6-sol",
        "openai:gpt-5.6-terra",
        "openai:gpt-5.6-luna",
    ):
        assert model in patched
    assert "CC_KIMI_AUTH_TOKEN" in patched
    assert "CC_ZAI_AUTH_TOKEN" in patched
    assert "CC_MINIMAX_AUTH_TOKEN" in patched
    assert "CC_OPENAI_PROXY_AUTH_TOKEN" in patched
    assert "CC_OPENAI_AVAILABLE" in patched
    assert '"baseURL":"http://127.0.0.1:17780"' in patched
    assert '"tokenEnv":"CC_OPENAI_PROXY_AUTH_TOKEN"' in patched
    assert '"availabilityEnv":"CC_OPENAI_AVAILABLE"' in patched
    assert "cc-openai-local" not in patched
    assert "apiKey:null,authToken:_ccToken,maxRetries:0" in patched
    assert "defaultHeaders:{..._ccInfo.definition.defaultHeaders}" in patched
    assert "_ccMultiProviderRoute(z,_ccRequest)" in patched
    assert "_ccClient.beta.messages.countTokens(_ccOutbound)" in patched
    assert '_ccMultiProviderDeniedRequestFields=["fallback_credit_token"]' in patched
    assert "_ccMultiProviderTraceHeaders.includes(_ccName.toLowerCase())" in patched
    assert "function _ccMultiProviderModelProvider(" in patched
    assert "function _ccMultiProviderCatalogInfo(" in patched
    assert "function _ccMultiProviderAttribution(" in patched
    assert '"attributionDomain":"kimi.com"' in patched
    assert '"attributionDomain":"z.ai"' in patched
    assert '"attributionDomain":"minimax.io"' in patched
    assert '"attributionDomain":"openai.com"' in patched
    attribution_start = patched.index("function umH()")
    attribution = patched[
        attribution_start : patched.index("function fU4(", attribution_start)
    ]
    assert "_ccMultiProviderAttribution(H,_ccNativeAttributionLabel)" in attribution
    assert "K=`Co-Authored-By: ${$} <noreply@${_ccAttributionDomain}>`" in attribution
    assert "noreply@anthropic.com" not in attribution
    assert 'if(_.includeCoAuthoredBy===!1)return{commit:"",pr:""}' in attribution
    assert (
        "if(_?.max_tokens&&_.max_tokens>=4096)q=_.max_tokens,$=Math.min($,q)" in patched
    )
    context_resolver = patched[
        patched.index("function K2(") : patched.index("function Hq7(")
    ]
    assert context_resolver.index("if(q!==void 0)return q") < context_resolver.index(
        "_ccProviderModel.contextWindow"
    )
    assert context_resolver.index(
        "_ccProviderModel.contextWindow"
    ) < context_resolver.index("if(XT6(H,$))return GEH")
    assert context_resolver.index("if(XT6(H,$))return GEH") < context_resolver.index(
        "return $q7(H,$)"
    )
    output_resolver = patched[
        patched.index("function AXH(") : patched.index("function qq7(")
    ]
    assert "q=_ccProviderModel.maxOutputTokens,$=Math.min($,q)" in output_resolver
    assert "$=q=_ccProviderModel.maxOutputTokens" not in output_resolver
    assert "if(K===\"claude-fable-5\"" in output_resolver
    assert "if(_?.max_tokens&&_.max_tokens>=4096)" in output_resolver
    compact_source = patched[
        patched.index("function i9$(") : patched.index("function a0f(")
    ]
    assert 'q==="auto"&&_ccMultiProviderCatalogInfo(H)!==null' in compact_source
    assert (
        patched.count("_ccMultiProviderModelProvider(q.message.model)!==_ccProvider")
        == 1
    )
    assert "q.message.model!==L0&&q.message.model!==$" not in patched
    assert "Z.filter((m$)=>_ccMultiProviderToolAllowed(J,m$)).map((m$)=>qx8" in patched
    assert '_ccTool.isMcp===!0||_ccTool.name!=="WebSearch"' in patched
    assert '"kimi:kimi-k3":{inputTokens:3,outputTokens:15' in patched
    assert '"zai:glm-5.3-flash":{inputTokens:0.15,outputTokens:0.5' in patched
    assert '"minimax:minimax-m3":{inputTokens:0.3,outputTokens:1.2' in patched
    assert '"kimi-k3":{inputTokens:' not in patched
    verification = patched[
        patched.index("async function xG9") : patched.index("function p7A")
    ]
    assert "_ccMultiProviderRoute" not in verification
    assert "source:\"verify_api_key\"" in verification


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
