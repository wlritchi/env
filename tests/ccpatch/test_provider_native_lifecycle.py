"""Exercise captured native workers and settings, not replacement implementations."""

from __future__ import annotations

import json
import os
import re
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
    _provider_env_207,
    _provider_key_sources,
    _replace_provider_snapshot,
)

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "platform", ["linux-x64", "linux-arm64", "darwin-x64", "darwin-arm64"]
)
def test_provider_207_settings_boundary(platform: str) -> None:
    path = ROOT / f"build/sweep-resume/2.1.207/{platform}/original.js"
    if not path.is_file():
        pytest.skip("requires captured pristine .207 source")
    runtime = shutil.which("node")
    if runtime is None:
        pytest.skip("requires node")
    original = path.read_text()
    snapshot = _PROVIDER_ENV_SNAPSHOT.search(original)
    assert snapshot is not None
    helpers = _replace_provider_snapshot(snapshot)
    patches = _provider_env_207(BACKGROUND_PROVIDER_ENV_198).patches
    install = next(
        p for p in patches if p.name == "install-provider-native-policy-boundary"
    )
    assert isinstance(install.replacement, str)
    helpers = install.pattern.sub(install.replacement, helpers)
    for name, count in [
        ("initialize-provider-before-native-settings", 2),
        ("filter-provider-settings-with-native-policy", 1),
    ]:
        patch = next(p for p in patches if p.name == name)
        assert len(list(patch.pattern.finditer(original))) == count
    initialize = next(
        p for p in patches if p.name == "initialize-provider-before-native-settings"
    )
    filter_patch = next(
        p for p in patches if p.name == "filter-provider-settings-with-native-policy"
    )
    native_filter = filter_patch.pattern.search(original)
    assert native_filter is not None
    assert not isinstance(filter_patch.replacement, str)
    filter_code = filter_patch.replacement(native_filter)
    assert isinstance(filter_code, str)
    filter_names = re.findall(r"([\w$]+)\(", native_filter["native"])
    filter_stubs = "".join(
        f"function {name}(env){{return env??{{}}}}" for name in filter_names
    )
    hidden_filter = filter_names[-1]
    hidden_start = original.index(f"function {hidden_filter}(")
    hidden_end = original.index("function ", hidden_start + 9)
    filter_stubs = filter_stubs.replace(
        f"function {hidden_filter}(env){{return env??{{}}}}",
        original[hidden_start:hidden_end],
    )
    native_initializers: list[str] = []
    initializer_tests: list[str] = []
    for match in initialize.pattern.finditer(original):
        end = min(
            original.index("function ", match.end()),
            original.index("var ", match.end()),
        )
        body = original[match.start() : end]
        patched_body = initialize.pattern.sub(initialize.replacement, body)
        native_initializers.append(patched_body)
        initializer_tests.append(
            f"assert.throws(()=>{match['apply']}(),{{code:'EPROVIDERENV'}});"
        )
    policy_getter = match["settings"]
    raw_policy_test = (
        f"function {policy_getter}(){{return {{env:{{ANTHROPIC_BASE_URL:'https://managed'}}}}}}"
        + "".join(native_initializers)
        + "process.env.ANTHROPIC_API_KEY='untouched';"
        + "".join(initializer_tests)
        + "assert.equal(process.env.ANTHROPIC_API_KEY,'untouched');"
        + "assert.equal(_ccProviderInitialized,false);"
        + filter_stubs
        + filter_code
        + "process.env.ANTHROPIC_UNIX_SOCKET='/synthetic/host.sock';"
        + f"assert.deepEqual({hidden_filter}({{ANTHROPIC_BASE_URL:'https://managed'}}),{{}});"
        + f"assert.throws(()=>{native_filter['filter']}({{ANTHROPIC_BASE_URL:'https://managed'}},'policySettings'),{{code:'EPROVIDERENV'}});"
        + "assert.equal(process.env.ANTHROPIC_API_KEY,'untouched');"
    )
    admin_match = re.search(
        r'function ([\w$]+)\(\)\{([\w$]+)\(\{sonnet:([\w$]+)\.ANTHROPIC_DEFAULT_SONNET_MODEL'
        r'!==void 0&&!([\w$]+)\("sonnet"\),opus:[^}]+\}\)\}',
        original,
    )
    assert admin_match is not None
    residue_start = original.index(f"function {admin_match[4]}(")
    residue_end = original.index("function ", residue_start + 9)
    admin_code = (
        f"var {admin_match[3]}=process.env;let admin;"
        f"function {admin_match[2]}(value){{admin=value}}"
        + original[residue_start:residue_end]
        + admin_match[0]
    )
    registry = "".join(
        f'var {name}=["ANTHROPIC_API_KEY","ANTHROPIC_BASE_URL","ANTHROPIC_DEFAULT_OPUS_MODEL","CLAUDE_CODE_3P_PROBE_WROTE_OPUS_DEFAULT"];'
        for name in _provider_key_sources(original)
    )
    script = (
        'const assert=require("node:assert/strict");'
        'process.env.CLAUDE_CODE_PROVIDER_ENV_TRANSIENT=JSON.stringify({'
        'ANTHROPIC_API_KEY:"requester",ANTHROPIC_BASE_URL:"https://requester",'
        'ANTHROPIC_DEFAULT_OPUS_MODEL:null});'
        + '_ccProviderInitialize();_ccProviderValidateManaged({},"policySettings");'
        + 'assert.deepEqual(_ccProviderFilterSettings({EARLY:"ok"},"userSettings"),{EARLY:"ok"});'
        + helpers
        + registry
        + raw_policy_test
        + admin_code
        + '''
        assert.equal(process.env.CLAUDE_CODE_PROVIDER_ENV_TRANSIENT,undefined);
        _ccProviderInitialize();
        assert.equal(process.env.ANTHROPIC_API_KEY,"requester");
        assert.deepEqual(_ccProviderFilterSettings({ANTHROPIC_API_KEY:"local",OTHER:"kept"},"userSettings"),{OTHER:"kept"});
        assert.deepEqual(_ccProviderFilterSettings({ANTHROPIC_BASE_URL:"https://requester",OTHER:"managed"},"policySettings"),{ANTHROPIC_BASE_URL:"https://requester",OTHER:"managed"});
        assert.throws(()=>_ccProviderValidateManaged({ANTHROPIC_BASE_URL:"https://managed"},"policySettings"),{code:"EPROVIDERENV"});
        process.env.ANTHROPIC_DEFAULT_OPUS_MODEL="native-fallback";
        process.env.CLAUDE_CODE_3P_PROBE_WROTE_OPUS_DEFAULT="native-fallback";
        _ccProviderInitialize();
        assert.equal(process.env.ANTHROPIC_DEFAULT_OPUS_MODEL,"native-fallback");
        assert.equal(process.env.CLAUDE_CODE_3P_PROBE_WROTE_OPUS_DEFAULT,"native-fallback");
        _ccProviderWorkerEnv=Object.freeze({..._ccProviderWorkerEnv,ANTHROPIC_DEFAULT_OPUS_MODEL:"requester-explicit"});
        _ccProviderInitialized=false;
        _ccProviderInitialize();
        NATIVE_ADMIN();
        assert.equal(admin.opus,true);
        assert.throws(()=>_ccProviderInitialize({ANTHROPIC_DEFAULT_OPUS_MODEL:"managed-explicit"}),{code:"EPROVIDERENV"});
        process.env.ANTHROPIC_DEFAULT_OPUS_MODEL="native-fallback";
        process.env.CLAUDE_CODE_3P_PROBE_WROTE_OPUS_DEFAULT="native-fallback";
        _ccProviderInitialize();
        NATIVE_ADMIN();
        assert.equal(admin.opus,false);
        _ccProviderWorkerEnv=null;
        const native={ANTHROPIC_API_KEY:"native"};
        assert.equal(_ccProviderFilterSettings(native,"policySettings"),native);
        '''.replace("NATIVE_ADMIN", admin_match[1])
    )
    result = subprocess.run(  # noqa: S603 - Run captured helpers with synthetic credentials.
        [runtime, "-e", script], capture_output=True, text=True, check=False, timeout=20
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _native_lifecycle_207() -> dict[str, str]:
    path = ROOT / "build/sweep-resume/2.1.207/linux-x64/original.js"
    if not path.is_file():
        pytest.skip("requires captured pristine .207 linux-x64 source")
    original = path.read_text()
    patched = _provider_env_207(BACKGROUND_PROVIDER_ENV_198).apply(original)
    # Rename minified bindings to the harness names. Do not replace native bodies.
    names = {
        'Bce': 'Yce',
        'As': 'xs',
        'wnn': 'yin',
        '_sn': 'uln',
        'Y3_': '_8_',
        'rLp': 'M$p',
        'z3_': 'g8_',
        'Ca': 'Ia',
        'pc': 'uc',
        'oy': 'sy',
        'JRp': 'I$p',
        'Yua': 'cma',
        'ZHe': 'TCe',
        'V3_': 'h8_',
        'nLp': '$$p',
        'j3_': 'd8_',
        'tLp': 'P$p',
        'qua': 'ima',
        'upr': 'Nfr',
        'mMo': '$Oo',
        'wC': 'CC',
        'eLp': 'D$p',
        'ZRp': 'L$p',
        'X0': 'J0',
        'xDs': 'D$s',
        'Uua': 'tma',
        'CRo': 'zDo',
        'Wua': 'oma',
        'jua': 'rma',
        'OSt': 'Svt',
        'uJe': 'JJe',
        'Nt': 'Bt',
        'yM': 'NM',
        'Rgt': '_yt',
        'QRp': 'R$p',
        'mMt': 'a$t',
        'lJ': 'TJ',
        'hMo': 'OOo',
        'Vua': 'sma',
        'zua': 'ama',
        'k_p': 'sAp',
        'gD': 'CD',
        'wta': 'Moa',
        'yke': 'Bke',
        '_gt': 'syt',
        'Qk': 'aI',
        'dV': 'Qj',
        'fr': 'pr',
        'u8e': 'O8e',
        '$le': 'Vle',
        'yXr': 'cQr',
        'XRp': 'k$p',
        'W3_': 'f8_',
        'YRp': 'x$p',
        'G3_': 'p8_',
        'cKo': 'xYo',
        'uKo': 'kYo',
        'jh': 'Wh',
        'JT': 'KA',
        'Gua': 'nma',
        'KRp': 'C$p',
        'Yo': 'Ko',
        'h8e': 'j8e',
        'SQe': 'lZe',
        'U3_': 'u8_',
        'qRp': 'w$p',
        'G$e': 'mOe',
        'S2e': 'N2e',
        'q3_': 'm8_',
        'bx_': 'BP_',
        'yx_': 'OP_',
        'Dr': 'Mr',
        'u$r': 'n1r',
        'S$i': 'V1i',
        'WRp': 'A$p',
        'Re': 'ke',
        'Bm': 'qm',
        'aRo': 'CDo',
        'Ft': 'Nt',
        'GRp': 'E$p',
        'jRp': 'v$p',
        'w_p': 'tAp',
        'qC_': 'dP_',
        'bsn': 'dln',
        'Kua': 'lma',
        'cTr': 'J0r',
        'VUt': 'V4t',
        'Rde': 'Qde',
        'ut': 'ct',
        'qUt': 'q4t',
        'rOa': 'mBa',
        'L4': 'Y4',
        'Tce': 'Mce',
        'umn': 'Xhn',
        'A5o': 'MWo',
        'kF': 'XF',
        'nNb': 'Vjb',
        'zut': 'Fdt',
        'cMt': 'r$t',
        'dOa': 'ABa',
        'd4': 'C4',
        'w_e': 'tbe',
        'vEt': 'iAt',
        'AI': 'II',
        'QTe': 'w0e',
        'Yxd': 'kDd',
        'Jxy': 'bMy',
        'Te': 'be',
        'ZWe': 'C6e',
        'xCs': 'DIs',
        'bir': 'Vsr',
        'gir': 'Gsr',
        'Sir': 'zsr',
        'ekd': 'PDd',
        'tye': 'Iye',
        'j7r': 'PXr',
        'Kyo': 'mSo',
        'vir': 'Ksr',
        'eky': 'HMy',
        'dg': 'fg',
        'sky': 'LMy',
        'tkd': 'MDd',
        'Zxy': 'vMy',
        'U7r': 'DXr',
        '_X': '$X',
        'Qxy': 'SMy',
        'lRt': 'QRt',
        'W7r': '$Xr',
        'rkd': '$Dd',
        'Ee': 'He',
        'Zxd': 'DDd',
        'Ct': 'xt',
        'cpr': 'Ofr',
        'HRo': 'VDo',
        'Ann': 'gin',
        'vnn': 'min',
        'gYe': 'QYe',
        'uOa': 'EBa',
    }
    names.update({"m6o": "L8o", "KSc": "Hwc", "s2n": "e4n"})
    reverse = {new: old for old, new in names.items()}
    tokens = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|[A-Za-z_$][\w$]*')

    def extract(start: str, end: str) -> str:
        offset = patched.index(start)
        fragment = patched[offset : patched.index(end, offset)]
        return tokens.sub(
            lambda match: (
                match[0]
                if match.start()
                and fragment[match.start() - 1] == "."
                and fragment[max(0, match.start() - 3) : match.start()] != "..."
                else reverse.get(match[0], match[0])
            ),
            fragment,
        )

    snapshot = _PROVIDER_ENV_SNAPSHOT.search(original)
    assert snapshot is not None
    helpers = _replace_provider_snapshot(snapshot)
    install = next(
        p
        for p in _provider_env_207(BACKGROUND_PROVIDER_ENV_198).patches
        if p.name == "install-provider-native-policy-boundary"
    )
    helpers = install.pattern.sub(install.replacement, helpers)
    registry = "".join(
        f'var {name}=["ANTHROPIC_API_KEY","ANTHROPIC_AUTH_TOKEN","ANTHROPIC_BASE_URL"];'
        for name in _provider_key_sources(original)
    )
    adoption = extract("await Promise.all(Object.entries(A.workers)", ",T+x+k>0)")
    adoption = adoption.replace("k++", "I++")
    return {
        "worker": extract("class Yce{", "var uln,nma,TJ,OOo,"),
        "stall": extract("function BP_(", "async function WAp("),
        "client": extract("function w$p(", "var A$p,"),
        "lines": extract("function CDo(", "var tAp,"),
        "environment": extract("function D$p(", "async function sma("),
        "sweeps": extract("async function mBa(", "function $jb(")
        + extract("async function Vjb(", "var Lf,fIf,ABa,"),
        "auth": extract("function w0e(", "var kDd,"),
        "endpoint": extract("async function bMy(", "async function ODd("),
        "server": extract("function vMy(", "async function ODd("),
        "adoption": adoption,
        "helpers": registry + helpers,
    }


@pytest.fixture(scope="module", params=[206, 207])
def native_lifecycle_source(request: pytest.FixtureRequest) -> dict[str, str]:
    if request.param == 207:
        return _native_lifecycle_207()
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


def test_provider_207_native_settings_and_policy() -> None:
    """Run native filters, both settings initializers, capture, and policy enforcement."""
    path = ROOT / "build/sweep-resume/2.1.207/linux-x64/original.js"
    if not path.is_file():
        pytest.skip("requires captured pristine .207 linux-x64 source")
    runtime = shutil.which("node")
    if runtime is None:
        pytest.skip("requires node")
    original = path.read_text()
    patches = _provider_env_207(BACKGROUND_PROVIDER_ENV_198).patches
    snapshot = _PROVIDER_ENV_SNAPSHOT.search(original)
    assert snapshot is not None
    helpers = _replace_provider_snapshot(snapshot)
    install = next(
        p for p in patches if p.name == "install-provider-native-policy-boundary"
    )
    helpers = install.pattern.sub(install.replacement, helpers)
    names = [
        "Bnt",
        "KY",
        "mQt",
        "f1g",
        "g1g",
        "y1g",
        "A1g",
        "_1g",
        "nNu",
        "Zro",
        "ACt",
        "zGl",
        "d8o",
        "p8o",
        "CPn",
        "Rj",
    ]
    fragments: list[str] = []
    for name in names:
        start = original.index(f"function {name}(")
        end = original.index("function ", start + 9)
        fragment = original[start:end].removesuffix("async ")
        if "}var " in fragment:
            fragment = fragment[: fragment.index("}var ") + 1]
        for patch in patches:
            if patch.name in {
                "initialize-provider-before-native-settings",
                "filter-provider-settings-with-native-policy",
            }:
                fragment = patch.pattern.sub(patch.replacement, fragment)
        fragments.append(fragment)
    registry = "".join(
        f'var {name}=["ANTHROPIC_API_KEY","ANTHROPIC_BASE_URL","ANTHROPIC_DEFAULT_OPUS_MODEL"];'
        for name in _provider_key_sources(original)
    )
    script = (
        r'''const assert=require("node:assert/strict");
    process.env.CLAUDE_CODE_PROVIDER_ENV_TRANSIENT=JSON.stringify({ANTHROPIC_API_KEY:"requester",ANTHROPIC_BASE_URL:"https://requester",ANTHROPIC_DEFAULT_OPUS_MODEL:null});
    const be=process.env; let policy=null, failures=[], policyReadable=true, Lyn;
    let Nnt={},dWr,hQt={},checks=0;
    const m1g=new Set(["policySettings","projectSettings","localSettings"]),h1g=new Set(["HOST_SECRET"]);
    const b1g=new Set(["CLAUDE_CODE_REMOTE"]),S1g=new Set(),v1g=new Set(),E1g=new Set(),z4t=new Set(),b1=new Set();
    const w1g=["userSettings","flagSettings","policySettings"];
    const ct=v=>v==="1",LQ=()=>false,g0n=k=>k==="ANTHROPIC_API_KEY",Aol=()=>false,y0n=()=>false;
    const Tr=s=>s==="policySettings"?policy:{env:{ANTHROPIC_API_KEY:"borrowed",OTHER:"local",HOST_SECRET:"hidden",CLAUDE_CODE_REMOTE:"hidden"}};
    const vt=()=>({env:{ANTHROPIC_API_KEY:"global"}}),Ph=()=>true,JA=()=>w1g,oNu=()=>{},jqn=()=>{};
    const check=()=>{assert.equal(be.ANTHROPIC_API_KEY,"requester");assert.equal(be.ANTHROPIC_BASE_URL,"https://requester");checks++};
    const pge=check,zHr=check,hHn=check,RYe=check,I2e=check,Voi=()=>({}),gjt=async()=>false,bjt=async()=>false,Ce=e=>{throw e};
    const FHr=()=>failures,UHr=()=>policyReadable,n2e=()=>"file",w=()=>{};
    const sl=(model,context)=>context.allowlist.includes(model),Ox=()=>false,rm=()=>false;
    '''
        + helpers
        + registry
        + "".join(fragments)
        + r'''
    policy={env:{ANTHROPIC_BASE_URL:"https://managed"}};
    assert.throws(()=>Bnt(),{code:"EPROVIDERENV"});
    assert.equal(be.ANTHROPIC_API_KEY,undefined);
    policy=null; Bnt(); Zro();
    assert.deepEqual(zGl(),{sonnet:false,opus:false});
    assert.equal(be.OTHER,"local");assert.equal(be.HOST_SECRET,undefined);assert.equal(be.CLAUDE_CODE_REMOTE,undefined);
    be.ANTHROPIC_DEFAULT_OPUS_MODEL="probe";be.CLAUDE_CODE_3P_PROBE_WROTE_OPUS_DEFAULT="probe";
    KY(); Zro();assert.equal(be.ANTHROPIC_DEFAULT_OPUS_MODEL,"probe");assert.equal(zGl().opus,false);
    be.ANTHROPIC_DEFAULT_OPUS_MODEL="explicit";Zro();assert.equal(zGl().opus,true);
    assert.ok(checks>3);
    Nnt.managedByHost=true;assert.deepEqual(mQt({ANTHROPIC_API_KEY:"requester",OTHER:"ok"},"policySettings"),{OTHER:"ok"});
    Nnt.managedByHost=false;dWr=new Set(["OTHER"]);assert.deepEqual(mQt({OTHER:"blocked"},"userSettings"),{});
    dWr=null;be.ANTHROPIC_UNIX_SOCKET="/native.sock";
    assert.deepEqual(mQt({ANTHROPIC_API_KEY:"requester",ANTHROPIC_BASE_URL:"https://requester"},"policySettings"),{});
    assert.throws(()=>mQt({ANTHROPIC_BASE_URL:"https://managed"},"policySettings"),{code:"EPROVIDERENV"});
    delete be.ANTHROPIC_UNIX_SOCKET;
    policy={availableModels:["allowed"],enforceAvailableModels:true,modelOverrides:{}};
    assert.equal(Rj("allowed"),true);assert.equal(Rj("explicit"),false);
    failures=["invalid policy"];policyReadable=false;assert.deepEqual(CPn(),{state:"refused"});assert.equal(Rj("allowed"),false);
    '''
    )
    result = subprocess.run(  # noqa: S603 - Run native functions with synthetic settings.
        [runtime, "-e", script], capture_output=True, text=True, check=False, timeout=20
    )
    assert result.returncode == 0, result.stdout + result.stderr
