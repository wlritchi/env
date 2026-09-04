"""Tests for the ported patch catalog (channels, dev-channel, syntax).

Each test uses a synthetic snippet that matches the real 2.1.170 minification.
The integration tests apply COMPACT_SESSION to a real binary.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

import pytest

from wlrenv.ccpatch.patches import (
    _KIMI_SYMBOLS,
    _KIMI_VERBS,
    _PROVIDER_ENV_EXPLICIT_KEYS,
    _PROVIDER_ENV_VERTEX_REGION_KEYS,
    _SKIP_ONBOARDING,
    _SYNTAX_DARK_MAP,
    BACKGROUND_PROVIDER_ENV,
    CATPPUCCIN_SYNTAX,
    CHANNELS_ENABLED,
    COMPACT_SESSION,
    DEV_CHANNEL_INHERITANCE,
    MULTI_PROVIDER_SDK,
    THINKING_SUMMARIES_NONINTERACTIVE,
    PatchError,
    PatchSet,
    _attribution_patch,
    _email_patch,
    _identity_patch,
    _model_costs_patch,
    _splash_patch,
    _startup_label_patch,
    _verb_symbol_patches,
    brand_patch_sets,
)

# Real 2.1.170 interactive-entry anchor: the point just past the print-mode early
# returns where onboarding is gated and (now) the splash is injected.
_ENTRY_SRC = (
    "if($$(!1)||process.env.IS_DEMO)"
    "return{onboardingShown:!1,mcpApprovalSkipWarning:A};"
    "let z=S$(),Y=!1;if(!z.hasCompletedOnboarding||"
    '(process.env.CLAUDE_CODE_TEAM_ONBOARDING==="banner"'
    '||process.env.CLAUDE_CODE_TEAM_ONBOARDING==="step")){Y=!0}'
)

# Real 2.1.170 startup-title anchor.
_LABEL_SRC = (
    'l=eu8?w1.createElement(eu8.Title,null):w1.createElement(y,{bold:!0},"Claude Code")'
)

_DEV_CHANNEL_SRC = (
    # respawn-flag allowlists the bg-worker dispatch filters argv through
    'RfH=new Set(["--advisor","--channels","--permission-prompt-tool","--tools"]),'
    'HE6=new Set(["--add-dir","--file","--channels"]);'
    # the live-dispatch arg serializer ($UH) that builds dispatchExtraArgs
    "function $UH(H){return [...H.settings?[\"--settings\",H.settings]:[],"
    '...H.pluginDir.flatMap(($)=>["--plugin-dir",$]),'
    '...H.mcpConfig.flatMap(($)=>["--mcp-config",$]),'
    '...H.strictMcpConfig?["--strict-mcp-config"]:[]]}'
    # the parent-side parse block, gated on !isNonInteractiveSession (XH)
    'if(W$&&W$.length>0)r$=c$(W$,"--channels"),n9H(r$);'
    "if(!XH){if(U$&&U$.length>0)"
    'Y$=c$(U$,"--dangerously-load-development-channels")}'
    'if(r$.length>0){d("tengu_mcp_channel_flags",{})}'
)

_MULTI_PROVIDER_SRC = (
    "let OPT={apiKey:key};return new SDK(OPT)}async function NEXT(){}"
    "function FILTER(MSGS,MODEL){return STRIP(MSGS,(MESSAGE)=>"
    "MESSAGE.message.model!==SYNTHETIC&&MESSAGE.message.model!==MODEL)}"
    "let R1=await N1.beta.messages.create({...REQ1,model:KA(REQ1.model)},"
    "{signal:SIG1.signal,timeout:TIME1,...Object.keys(HDR1).length>0&&{headers:HDR1}})"
    "let R2=await N2.beta.messages.create({...REQ2,...CREDIT!==void 0&&"
    "{fallback_credit_token:CREDIT},stream:!0},{signal:SIG2,...Object.keys(HDR2).length>0&&"
    "{headers:HDR2}})"
    "let START=performance.now(),R3=await N3.beta.messages.create(REQ3,"
    "{signal:SIG3,...TIME3!==void 0&&{timeout:TIME3}})"
    'async function TOKENS(MSGS,TOOLS,MODEL){return WRAP(MSGS,TOOLS,async()=>{try{'
    'let EFFECTIVE=MODEL??DEFAULT();let CLIENT=await LF({maxRetries:1,model:EFFECTIVE,'
    'source:"count_tokens"}),'
    "BETAS=RAW.filter((BETA)=>ij6.has(BETA)),RESULT=await CLIENT.beta.messages."
    "countTokens({model:KA(EFFECTIVE),messages:MSGS,tools:TOOLS});return RESULT.input_tokens"
    "}catch(ERROR){return N(`countTokens API call failed: ${ERROR.message}`),null}})}"
    "function PICK(FLAG){let OPTIONS=NATIVE(FLAG),CUSTOM=process.env."
    "ANTHROPIC_CUSTOM_MODEL_OPTION;"
    "function RECOGNIZE(MODEL){let NAME=DISPLAY(MODEL);if(!NAME)return null;"
    'let NORMALIZED=NORMALIZE(MODEL),ALIAS=null;if(NORMALIZED.includes("fable"))'
)

_PROVIDER_ENV_SRC = (
    'SEL=["CLAUDE_CODE_USE_BEDROCK","CLAUDE_CODE_USE_VERTEX"],'
    'URLS=["ANTHROPIC_BASE_URL","ANTHROPIC_VERTEX_BASE_URL"],'
    'CREDS=["ANTHROPIC_API_KEY","ANTHROPIC_AUTH_TOKEN","CLAUDE_CODE_OAUTH_TOKEN"],'
    'SKIP=["CLAUDE_CODE_SKIP_BEDROCK_AUTH","CLAUDE_CODE_SKIP_VERTEX_AUTH",'
    '"CLAUDE_CODE_SKIP_FOUNDRY_AUTH","CLAUDE_CODE_SKIP_ANTHROPIC_AWS_AUTH",'
    '"CLAUDE_CODE_SKIP_MANTLE_AUTH"],'
    'MODELS=["ANTHROPIC_MODEL","ANTHROPIC_DEFAULT_SONNET_MODEL"],'
    'CUSTOM=["ANTHROPIC_CUSTOM_MODEL_OPTION","ANTHROPIC_CUSTOM_MODEL_OPTION_NAME"],'
    'KNOWN=new Set;'
    'function SNAP(){let out={};for(let key of OLD){let value=process.env[key];'
    'if(value===void 0)continue;if(value===""&&key!=="CLAUDE_SECURESTORAGE_CONFIG_DIR")'
    'continue;out[key]=value}return out}'
    'iso=source==="repl"?"none":opts?.bgIsolation,provider=opts?.providerEnv??SNAP(),'
    'async function write(dir,state){let{pinned:a,sortOrder:b,stateSortOrder:c,...rest}=state;'
    'bgIsolation:job.bgIsolation,providerEnv:job.providerEnv,'
    'providerEnv:z.record(z.string(),z.string()).transform(filter).optional(),'
    'providerEnv:prior?.providerEnv,providerEnv:SNAP(),sessionPermissionRules:'
    '||job.providerEnv,...job.providerEnv&&{providerEnv:job.providerEnv}'
    'z.object({proto:p,op:z.literal("dispatch"),d:dispatch(),timeoutMs:z.number(),auth:'
    'send({proto:PROTO,op:"dispatch",d:{...job,nonce:nonce},timeoutMs:5000,'
    'auth:await auth()}'
    'if(reply.ok&&reply.op==="dispatch")return success();if("code"in reply&&'
    'send({proto:PROTO,op:"dispatch",d:{...retryJob,nonce:retryNonce},timeoutMs:5000,'
    'auth:await retryAuth()}'
    'if(retryReply.ok&&retryReply.op==="dispatch")return log(),await metric(),'
    '{ok:!0,short:shortName,sessionId:sessionId,idle:idle,name:name,rescued:!0}'
    'return respond(sock,{ok:!0,op:operation,short:short,pid:worker.record.pid,'
    'case"dispatch":if(!check(req.auth,control))return respond(sock,{ok:!1,'
    'error:"bad",code:"EAUTH"});if(await pause(0),sock.readableEnded||sock.destroyed)'
    '{metric();return}return wait(handles,sock,"dispatch",req.d.short,req.d.nonce,'
    'req.timeoutMs,dispatchCb(req.d)'
    'Worker{dispatch;spawnPty;getAuthSnapshot;via;record;'
    'constructor(job,spawn,authFn,via,record){this.dispatch=job;'
    'static spawn(job,spawn,authFn,options){let worker=new Worker(job,spawn??defaultSpawn(),'
    'authFn,"cold");'
    'static claim(job,options){let worker=new Worker(job,options.spawnPty,'
    'options.getAuthSnapshot,"spare",{pid:spare.pid,cliVersion:BUILD.VERSION});'
    'static buildClaimFrame(job,snapshot,auth){let dir=jobDir(job.short),'
    'env=buildEnv(job,dir,snapshot,rvSock(job.short),auth);'
    'let argv=buildArgv(job,this.attempt,messages,session,flags),'
    'env=buildEnv(job,dir,snapshot,this.rvSockPath??rvSock(job.short),this.socketAuth());'
    'function W0q(job,spare,spawn,auth){let worker=Worker.claim(job,{pid:spare.hostPid,'
    'ptySockPath:spare.ptySock,spawnPty:spawn,getAuthSnapshot:auth});'
    'return fetchSnapshot(job.short,auth?.()).then((snapshot)=>send(spare.claimSock,'
    'UVA(job,snapshot,worker.socketAuth(),spare.claimAuth)))'
    'function UVA(job,snapshot,auth,claimAuth){let{env:built,argv:args}='
    'Worker.buildClaimFrame(job,snapshot,auth);'
    'manage=async(job,retry=0,afterUpgrade)=>{'
    'return await delay(100),manage(job,retry+1,afterUpgrade)'
    'pendingUpgrade=()=>manage(saved.dispatch,0,!0),'
    'dispatchFiles=watch((incoming)=>void manage(incoming).catch(logError)),'
    'internal={dispatch:(incoming)=>void manage(incoming).catch(logError)},'
    'adoptedRespawn=(incoming)=>manage({...incoming,source:"respawn"}),'
    'let worker=W0q(job,spare,spawn,authObj.getAuthSnapshot)'
    'Worker.spawn(job,spawn,authObj.getAuthSnapshot,afterUpgrade?'
    '{afterUpgrade:afterUpgrade}:void 0)'
    'function buildEnv(job,dir,snapshot,rvSock,socketAuth){let ambient={...process.env},'
    'env={...ambient,...job.env};if(process.env.CLAUDE_CONFIG_DIR)'
    'env.CLAUDE_CONFIG_DIR=process.env.CLAUDE_CONFIG_DIR;for(let key of UXq)'
    'if(!job.env?.[key])delete env[key];for(let key of FXq)if(!job.env?.[key])'
    'delete env[key];for(let key of Object.keys(env))if(VERTEX.some((prefix)=>'
    'key.startsWith(prefix))&&!job.env?.[key])delete env[key];if(providerManaged(ambient))'
    '{for(let key of CREDS)delete env[key]}return env}'
    'async function O09(claimFrame,mainModule){let cwd=await resolveCwd(claimFrame.cwd);'
    'process.chdir(cwd),sessionSetup(),Object.assign(process.env,claimFrame.env),'
    'process.argv=[process.argv[0],...claimFrame.argv],It8(),Da8(),lT6(),Yq8(),Wy$();'
    'let{main:workerMain}=await mainModule;await workerMain()}'
    'function CB$(){if(remote()){log("Waiting for remote managed settings before telemetry init"),'
    'Vy$().then(async()=>{log("Remote managed settings loaded, initializing telemetry"),'
    'Ko(),await vLq()}).catch(logError)}else vLq().catch(logError)}'
    '.hook("preAction",async(command,action)=>{mark("preAction_start");let start=now();'
    'if(await initializeMdm(),mark("preAction_after_mdm"),await settingsInit(),'
    'mark("preAction_after_init"),terminalTitle())setTitle();'
    'if(noninteractive){setMode(),Ko(),CB$();let operationalStart=performance.now(),'
)


def test_channels_enabled() -> None:
    src = 'a&&H?.channelsEnabled!==!0;function zH(){return M$("tengu_harbor",!1)}'
    out = CHANNELS_ENABLED.apply(src)
    assert "a&&!1;" in out
    assert "return !!1}" in out
    assert "tengu_harbor" not in out


def test_dev_channel_inheritance_threads_natively() -> None:
    out = DEV_CHANNEL_INHERITANCE.apply(_DEV_CHANNEL_SRC)
    # flag forwarded through both respawn allowlists (value + multi-value)
    assert (
        '"--channels","--dangerously-load-development-channels",'
        '"--permission-prompt-tool"' in out
    )
    assert '"--file","--channels","--dangerously-load-development-channels"]' in out
    # live dispatch: $UH serializer appends dev-channels (scanned from argv)
    assert 'strictMcpConfig?["--strict-mcp-config"]:[],...(()=>' in out
    assert (
        'return _dc.length?["--dangerously-load-development-channels",..._dc]:[]' in out
    )
    # bg worker registers dev channels from its OWN parsed flag (no env round-trip),
    # reusing the registrar/base/parse identifiers captured from the block
    assert (
        'CLAUDE_CODE_SESSION_KIND==="bg"&&U$&&U$.length>0){'
        'n9H([...r$,...c$(U$,"--dangerously-load-development-channels")' in out
    )
    assert "devEntry,dev:!0}))])}" in out  # brace-terminated (no `)if(` syntax error)
    assert "CLAUDE_DEV_CHANNELS" not in out  # the env round-trip is gone
    # the original telemetry block is preserved (length-free append)
    assert 'd("tengu_mcp_channel_flags",{})' in out


def test_dev_channel_required_no_op_fails() -> None:
    with pytest.raises(PatchError, match="dev-channel-inheritance"):
        DEV_CHANNEL_INHERITANCE.apply("unrelated source")


@pytest.mark.parametrize(
    ("version", "expected"),
    (
        (None, False),
        ((2, 1, 173), False),
        ((2, 1, 174), True),
        ((2, 1, 175), False),
    ),
)
def test_background_provider_environment_is_version_gated(
    version: tuple[int, ...] | None, expected: bool
) -> None:
    assert BACKGROUND_PROVIDER_ENV.applies_to(version) is expected
    assert MULTI_PROVIDER_SDK.applies_to(version) is expected


def test_patch_sets_allow_omitted_version_by_default() -> None:
    assert CHANNELS_ENABLED.applies_to(None)


def test_background_provider_environment_transforms_complete_fixture() -> None:
    patched = BACKGROUND_PROVIDER_ENV.apply(_PROVIDER_ENV_SRC)

    assert (
        "function _ccProviderKeys(){if(OLD==null||SEL==null||URLS==null||"
        "CREDS==null||SKIP==null||MODELS==null||CUSTOM==null)throw " in patched
    )
    assert (
        "return [...OLD,...SEL,...URLS,...CREDS,...SKIP,...MODELS,...CUSTOM" in patched
    )
    assert "function _ccRequireProviderEnv(_ccProviderEnv)" in patched
    assert "_ccProviderEnv=_ccProviderRetain(_ccProviderEnv);" in patched
    assert "let _ccAllowed=new Set(_ccProviderKeys())" in patched
    for key in _PROVIDER_ENV_EXPLICIT_KEYS:
        assert f'"{key}"' in patched
    assert "out[key]=value===void 0?null:value" in patched
    assert patched.count("providerEnvVersion:2,providerEnv:SNAP()") == 2
    assert (
        "z.record(z.enum(_ccProviderKeys()),z.union([z.string(),z.null()]))" in patched
    )
    assert "req.providerEnvVersion!==2||req.providerEnv===void 0" in patched
    assert "op:operation,providerEnvVersion:2,short:short" in patched
    assert "reply.providerEnvVersion!==2" in patched
    assert "retryReply.providerEnvVersion!==2" in patched
    assert "Restart the stale Claude Code daemon and try again" in patched
    assert 'reply.code==="EPROVIDERENV"' in patched
    assert 'retryReply.code==="EPROVIDERENV"' in patched
    assert 'reason:"daemon-unreachable",detail:reply.error' in patched
    assert (
        'throw Object.assign(Error(retryReply.error),{code:"EPROVIDERENV"})' in patched
    )
    assert "dispatchCb(req.d,0,void 0,req.providerEnv)" in patched
    assert (
        'new Worker(job,spawn??defaultSpawn(),authFn,"cold",_ccProviderEnv)' in patched
    )
    assert "this.socketAuth(),this.providerEnv" in patched
    assert "Worker.claim(job," in patched and "_ccProviderEnv" in patched
    assert "function W0q(job,spare,spawn,auth,_ccProviderEnv)" in patched
    assert (
        "UVA(job,snapshot,worker.socketAuth(),spare.claimAuth,_ccProviderEnv)"
        in patched
    )
    assert "function UVA(job,snapshot,auth,claimAuth,_ccProviderEnv)" in patched
    assert "Worker.buildClaimFrame(job,snapshot,auth,_ccProviderEnv)" in patched
    assert "manage(job,retry+1,afterUpgrade,_ccProviderEnv)" in patched
    assert "for(let _ccKey of _ccProviderKeys())delete env[_ccKey]" in patched
    assert "Object.entries(_ccProviderEnv)" in patched
    assert "for(let _ccKey of _ccProviderKeys())delete process.env[_ccKey]" in patched
    assert "providerEnv:_ccProviderEnv,...rest" in patched
    assert "providerEnv:job.providerEnv" not in patched
    assert "providerEnv:prior?.providerEnv" not in patched
    assert "providerEnv:SNAP(),sessionPermissionRules" not in patched
    assert "...job.providerEnv&&{providerEnv:job.providerEnv}" not in patched
    file_fallback = 'filePayload=serialize({...job,nonce})'
    assert "providerEnv" not in file_fallback


def test_background_provider_environment_startup_defers_key_spreads() -> None:
    runtime = shutil.which("node") or shutil.which("bun")
    if runtime is None:
        pytest.skip("no node/bun to evaluate the generated provider key function")
    patched = BACKGROUND_PROVIDER_ENV.apply(_PROVIDER_ENV_SRC)
    start = patched.index("function _ccProviderKeys()")
    end = patched.index("function SNAP()", start)
    generated = patched[start:end]
    script = (
        'let OLD,SEL,URLS,CREDS,SKIP,MODELS,CUSTOM;'
        + generated
        + ';console.log("startup-ok");'
        + 'try{_ccProviderKeys()}catch(error){'
        + 'console.log(`${error.code}:${error.message}`)}'
    )

    proc = subprocess.run(  # noqa: S603 - runtime is which()-resolved node/bun
        [runtime, "-e", script], capture_output=True, text=True, timeout=30
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.splitlines() == [
        "startup-ok",
        "EPROVIDERENV:Background provider environment key registry is unavailable",
    ]


def test_background_provider_environment_validates_transient_payload() -> None:
    runtime = shutil.which("node") or shutil.which("bun")
    if runtime is None:
        pytest.skip("no node/bun to evaluate the generated provider validator")
    patched = BACKGROUND_PROVIDER_ENV.apply(_PROVIDER_ENV_SRC)
    start = patched.index("function _ccProviderKeys()")
    end = patched.index("function SNAP()", start)
    generated = patched[start:end]
    script = (
        'let OLD=["ANTHROPIC_API_KEY"],SEL=[],URLS=[],CREDS=[],SKIP=[],'
        + "MODELS=[],CUSTOM=[];"
        + generated
        + ';for(const [label,value] of [["valid",{ANTHROPIC_API_KEY:"current"}],'
        + '["cleared",{ANTHROPIC_API_KEY:null}],["absent",undefined],'
        + '["null",null],["array",[]],["unknown",{OTHER:"stale"}],'
        + '["wrong-type",{ANTHROPIC_API_KEY:7}]]){try{'
        + '_ccRequireProviderEnv(value);console.log(`${label}:ok`)}catch(error){'
        + 'console.log(`${label}:${error.code}:${error.message}`)}}'
    )

    proc = subprocess.run(  # noqa: S603 - runtime is which()-resolved node/bun
        [runtime, "-e", script], capture_output=True, text=True, timeout=30
    )

    assert proc.returncode == 0, proc.stderr
    unavailable = (
        "EPROVIDERENV:Background provider environment transient payload is unavailable; "
        "dispatch again from the current Claude Code session"
    )
    invalid = (
        "EPROVIDERENV:Background provider environment transient payload is invalid; "
        "dispatch again from the current Claude Code session"
    )
    assert proc.stdout.splitlines() == [
        "valid:ok",
        "cleared:ok",
        f"absent:{unavailable}",
        f"null:{unavailable}",
        f"array:{unavailable}",
        f"unknown:{invalid}",
        f"wrong-type:{invalid}",
    ]


@pytest.mark.parametrize(
    ("caller", "call"),
    (
        ("pending-upgrade recovery", "manage(saved.dispatch,0,!0)"),
        ("dispatch-file ingestion", "manage(incoming)"),
        ("exposed internal dispatch callback", "manage(incoming)"),
        ("adopted-worker respawn", 'manage({...incoming,source:"respawn"})'),
    ),
)
def test_background_provider_environment_rejects_manager_callers_without_snapshot(
    caller: str, call: str
) -> None:
    patched = BACKGROUND_PROVIDER_ENV.apply(_PROVIDER_ENV_SRC)
    assert call in patched, caller
    manager_start = patched.index(
        "manage=async(job,retry=0,afterUpgrade,_ccProviderEnv)=>{"
    )
    manager_body = patched[manager_start : manager_start + 250]
    assert "_ccProviderEnv=_ccProviderRetain(_ccProviderEnv);" in manager_body, caller


@pytest.mark.parametrize(
    "path",
    (
        "socket primary",
        "socket retry/recovery",
        "claimed spare",
        "cold worker",
    ),
)
def test_background_provider_environment_threads_current_requester_snapshot(
    path: str,
) -> None:
    patched = BACKGROUND_PROVIDER_ENV.apply(_PROVIDER_ENV_SRC)
    markers = {
        "socket primary": "providerEnvVersion:2,providerEnv:SNAP()",
        "socket retry/recovery": "providerEnvVersion:2,providerEnv:SNAP()",
        "claimed spare": "W0q(job,spare,spawn,authObj.getAuthSnapshot,_ccProviderEnv)",
        "cold worker": (
            "Worker.spawn(job,spawn,authObj.getAuthSnapshot,afterUpgrade?"
            "{afterUpgrade:afterUpgrade}:void 0,_ccProviderEnv)"
        ),
    }
    minimum_counts = {"socket retry/recovery": 2}
    assert patched.count(markers[path]) >= minimum_counts.get(path, 1)


@pytest.mark.parametrize(
    "worker_path",
    ("claimed spare", "cold worker", "worker respawn"),
)
def test_background_provider_environment_worker_creation_rejects_absent_payload(
    worker_path: str,
) -> None:
    patched = BACKGROUND_PROVIDER_ENV.apply(_PROVIDER_ENV_SRC)
    markers = {
        "claimed spare": "Worker.buildClaimFrame(job,snapshot,auth,_ccProviderEnv)",
        "cold worker": 'new Worker(job,spawn??defaultSpawn(),authFn,"cold",_ccProviderEnv)',
        "worker respawn": "this.socketAuth(),this.providerEnv",
    }
    assert markers[worker_path] in patched
    assert "_ccProviderEnv=_ccProviderRetain(_ccProviderEnv);" in patched


def test_background_provider_environment_covers_upstream_vertex_region_keys() -> None:
    assert _PROVIDER_ENV_VERTEX_REGION_KEYS == (
        "VERTEX_REGION_CLAUDE_FABLE_5",
        "VERTEX_REGION_CLAUDE_HAIKU_4_5",
        "VERTEX_REGION_CLAUDE_3_5_HAIKU",
        "VERTEX_REGION_CLAUDE_3_5_SONNET",
        "VERTEX_REGION_CLAUDE_3_7_SONNET",
        "VERTEX_REGION_CLAUDE_4_8_OPUS",
        "VERTEX_REGION_CLAUDE_4_7_OPUS",
        "VERTEX_REGION_CLAUDE_4_6_OPUS",
        "VERTEX_REGION_CLAUDE_4_5_OPUS",
        "VERTEX_REGION_CLAUDE_4_1_OPUS",
        "VERTEX_REGION_CLAUDE_4_0_OPUS",
        "VERTEX_REGION_CLAUDE_4_6_SONNET",
        "VERTEX_REGION_CLAUDE_4_5_SONNET",
        "VERTEX_REGION_CLAUDE_4_0_SONNET",
    )


def test_background_provider_environment_final_apply_follows_initializers() -> None:
    patched = BACKGROUND_PROVIDER_ENV.apply(_PROVIDER_ENV_SRC)

    claimed_start = patched.index("async function O09")
    claimed = patched[claimed_start : claimed_start + 900]
    capture = "_ccProviderWorkerEnv=_ccProviderCaptureTransport(claimFrame.env)"
    assert (
        claimed.index(capture)
        < claimed.index("It8(),Da8(),lT6(),Yq8(),Wy$()")
        < claimed.index("_ccProviderApplyWorkerFinal()")
        < claimed.index("await workerMain()")
    )
    assert "_ccProviderSnapshotFromEnv" not in claimed

    builder_start = patched.index("function buildEnv(")
    builder = patched[
        builder_start : patched.index("async function O09", builder_start)
    ]
    payload = "let _ccProviderPayload=_ccProviderRetain(_ccProviderEnv)"
    initial_apply = "Object.entries(_ccProviderPayload)"
    native_scrub = "for(let key of FXq)"
    final_apply = "Object.entries(_ccProviderPayload)"
    assert builder.index(payload) < builder.index(initial_apply)
    assert builder.index(initial_apply) < builder.index(native_scrub)
    assert builder.index(native_scrub) < builder.rindex(final_apply)
    assert builder.rindex(final_apply) < builder.index("return env}")
    assert "_ccProviderSnapshotFromEnv" not in builder

    preaction = patched[
        patched.index('.hook("preAction"') : patched.index("setTitle()")
    ]
    assert preaction.index("await settingsInit()") < preaction.index(
        "_ccProviderApplyWorkerFinal()"
    )

    operational = patched[
        patched.index("if(noninteractive)") : patched.index("operationalStart")
    ]
    assert operational.index("Ko(),CB$(_ccProviderWorkerEnv)") < operational.index(
        "_ccProviderApplyWorkerFinal()"
    )

    delayed_start = patched.index("function CB$")
    delayed = patched[delayed_start : delayed_start + 700]
    assert (
        delayed.index("Ko()")
        < delayed.index("_ccProviderApplyWorkerFinal()")
        < delayed.index("await vLq()")
    )


@pytest.mark.parametrize(
    "worker_path",
    ("cold", "claimed", "respawn", "retry", "adopted-respawn"),
)
def test_background_provider_environment_mutable_worker_paths_reach_final_apply(
    worker_path: str,
) -> None:
    patched = BACKGROUND_PROVIDER_ENV.apply(_PROVIDER_ENV_SRC)
    path_markers = {
        "cold": "Worker.spawn(job,spawn,authObj.getAuthSnapshot",
        "claimed": "W0q(job,spare,spawn,authObj.getAuthSnapshot,_ccProviderEnv)",
        "respawn": "this.socketAuth(),this.providerEnv",
        "retry": "manage(job,retry+1,afterUpgrade,_ccProviderEnv)",
        "adopted-respawn": 'manage({...incoming,source:"respawn"})',
    }

    assert path_markers[worker_path] in patched
    assert "let _ccProviderWorkerEnv=_ccProviderCaptureTransport()" in patched
    assert (
        "CLAUDE_CODE_PROVIDER_ENV_TRANSIENT=JSON.stringify(_ccProviderPayload)"
        in patched
    )
    assert "_ccProviderSnapshotFromEnv" not in patched
    assert "Ko(),CB$(_ccProviderWorkerEnv);_ccProviderApplyWorkerFinal()" in patched


def test_background_provider_environment_regression_harness() -> None:
    runtime = shutil.which("node") or shutil.which("bun")
    if runtime is None:
        pytest.skip("no node/bun to run the provider environment regression harness")
    harness = Path(__file__).with_name("provider_env_regression.mjs")

    proc = subprocess.run(  # noqa: S603 - runtime is which()-resolved node/bun
        [runtime, str(harness)], capture_output=True, text=True, timeout=30
    )

    assert proc.returncode == 0, proc.stderr
    assert (
        proc.stdout.strip() == "provider environment transient transport ordering: ok"
    )


def test_background_provider_environment_new_keys_cover_full_lifecycle() -> None:
    patched = BACKGROUND_PROVIDER_ENV.apply(_PROVIDER_ENV_SRC)
    keys = ("CLAUDE_CODE_CERT_STORE", *_PROVIDER_ENV_VERTEX_REGION_KEYS)

    for key in keys:
        assert patched.count(f'"{key}"') == 1
    assert "out[key]=value===void 0?null:value" in patched
    assert "for(let _ccKey of _ccProviderKeys())delete env[_ccKey]" in patched
    assert "for(let _ccKey of _ccProviderKeys())delete process.env[_ccKey]" in patched
    assert "Object.entries(_ccProviderEnv)" in patched
    assert "if(_ccValue!==null)env[_ccKey]=_ccValue" in patched
    assert (
        "z.record(z.enum(_ccProviderKeys()),z.union([z.string(),z.null()]))" in patched
    )


def test_background_provider_environment_strict_receiver_rejects_unknown_keys() -> None:
    schema = BACKGROUND_PROVIDER_ENV.apply(_PROVIDER_ENV_SRC)
    assert "z.record(z.enum(_ccProviderKeys())" in schema
    assert "z.record(z.string()" not in schema[schema.index("providerEnvVersion:") :]


def test_background_provider_environment_rejects_protocol_skew() -> None:
    patched = BACKGROUND_PROVIDER_ENV.apply(_PROVIDER_ENV_SRC)
    assert "req.providerEnvVersion!==2" in patched
    assert "req.providerEnv===void 0" in patched
    assert 'code:"EPROVIDERENV"' in patched
    assert 'reply.code==="EPROVIDERENV"' in patched


def test_background_provider_environment_rejects_old_daemon_success() -> None:
    patched = BACKGROUND_PROVIDER_ENV.apply(_PROVIDER_ENV_SRC)
    old_daemon_success = (
        'reply={ok:!0,op:"dispatch"};'
        + patched[patched.index('if(reply.ok&&reply.op==="dispatch")') :]
    )
    assert "reply.providerEnvVersion!==2" in old_daemon_success
    assert 'reply={ok:!1,error:"Background provider environment protocol mismatch.' in (
        old_daemon_success
    )
    assert 'code:"EPROVIDERENV"' in old_daemon_success


def test_background_provider_environment_rejects_old_daemon_redispatch() -> None:
    patched = BACKGROUND_PROVIDER_ENV.apply(_PROVIDER_ENV_SRC)
    redispatch = patched[
        patched.index('if(retryReply.ok&&retryReply.op==="dispatch")') :
    ]

    assert "retryReply.providerEnvVersion!==2" in redispatch
    assert (
        'retryReply={ok:!1,error:"Background provider environment protocol mismatch.'
        in redispatch
    )
    assert 'retryReply.code==="EPROVIDERENV"' in redispatch
    assert (
        'throw Object.assign(Error(retryReply.error),{code:"EPROVIDERENV"})'
        in redispatch
    )
    assert redispatch.index("retryReply.providerEnvVersion!==2") < redispatch.index(
        "return log(),await metric()"
    )


def test_background_provider_environment_required_no_op_fails() -> None:
    with pytest.raises(PatchError, match="background-provider-environment"):
        BACKGROUND_PROVIDER_ENV.apply("unrelated source")


def test_background_provider_environment_rejects_partial_source() -> None:
    source = _PROVIDER_ENV_SRC.replace(
        '"ANTHROPIC_API_KEY","ANTHROPIC_AUTH_TOKEN",', '"ANTHROPIC_API_KEY",'
    )
    with pytest.raises(PatchError, match="provider groups absent"):
        BACKGROUND_PROVIDER_ENV.apply(source)


def test_multi_provider_sdk_transforms_complete_fixture() -> None:
    patched = MULTI_PROVIDER_SDK.apply(_MULTI_PROVIDER_SRC)

    assert "const _ccMultiProviderSDK=()=>SDK" in patched
    assert patched.count("_ccMultiProviderRoute(") == 5
    assert "_ccMultiProviderRoute(N1,_ccRequest,_ccOptions)" in patched
    assert "_ccMultiProviderRoute(N2,_ccRequest,_ccOptions)" in patched
    assert "_ccMultiProviderRoute(N3,_ccRequest,_ccOptions)" in patched
    assert "_ccMultiProviderRoute(CLIENT,_ccRequest)" in patched
    assert "MESSAGE.message.model!==MODEL" not in patched
    assert "MESSAGE.message.model!==SYNTHETIC" in patched
    assert (
        "_ccMultiProviderModelProvider(MESSAGE.message.model)!==_ccProvider" in patched
    )
    assert "countTokens(_ccOutbound)" in patched
    assert "model:KA(_ccEffectiveModel)" in patched
    assert "_ccEffectiveModelA(_ccEffectiveModel)" not in patched
    assert (
        "return _ccMultiProviderInputTokens(_ccEffectiveModel,RESULT)}catch(ERROR)"
        in patched
    )
    assert "if(_ccMultiProviderModelInfo(_ccEffectiveModel))throw ERROR" in patched
    assert 'code:"EPROVIDERINCOMPATIBLE"' in patched
    assert "model:KA(REQ1.model)" in patched
    assert "OPTIONS.push(..._ccMultiProviderCatalog)" in patched
    assert "_ccMultiProviderCatalog.find" in patched
    assert "CC_KIMI_AUTH_TOKEN" in patched
    assert "CC_ZAI_AUTH_TOKEN" in patched
    assert "CC_MINIMAX_AUTH_TOKEN" in patched
    assert "apiKey:null,authToken:_ccToken,maxRetries:0" in patched
    assert "defaultHeaders:{..._ccInfo.definition.defaultHeaders}" in patched
    assert '_ccMultiProviderDeniedRequestFields=["fallback_credit_token"]' in patched
    assert 'code:"EPROVIDERCREDENTIAL"' in patched
    assert "_ccMultiProviderTraceHeaders.includes(_ccName.toLowerCase())" in patched


def test_multi_provider_sdk_required_no_op_fails() -> None:
    with pytest.raises(PatchError, match="multi-provider-sdk"):
        MULTI_PROVIDER_SDK.apply("unrelated source")


def test_multi_provider_sdk_regression_harness(tmp_path: Path) -> None:
    runtime = shutil.which("node") or shutil.which("bun")
    if runtime is None:
        pytest.skip("no node/bun to run the multi-provider regression harness")
    harness = Path(__file__).with_name("multi_provider_regression.mjs")
    helper = tmp_path / "multi_provider_helper.js"
    generated = subprocess.run(  # noqa: S603 - current Python interpreter
        [
            sys.executable,
            "-m",
            "wlrenv.ccpatch.cli",
            "generate-multi-provider-helper",
            "-o",
            str(helper),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert generated.returncode == 0, generated.stderr
    proc = subprocess.run(  # noqa: S603 - runtime is which()-resolved node/bun
        [runtime, str(harness), str(helper)],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "multi-provider SDK routing: ok"


def test_thinking_summaries_ungated_for_noninteractive() -> None:
    src = (
        'n8.type!=="disabled"){if(A.thinkingDisplay==="summarized"||'
        'A.thinkingDisplay==="omitted")n8.display=A.thinkingDisplay;'
        'else if(!F6()&&O78())n8.display="summarized"}rest'
    )
    out = THINKING_SUMMARIES_NONINTERACTIVE.apply(src)
    assert 'else if(O78())n8.display="summarized"' in out  # interactive gate gone
    assert "!F6()&&" not in out
    # the explicit --thinking-display branch is untouched
    assert (
        'A.thinkingDisplay==="summarized"||A.thinkingDisplay==="omitted")'
        "n8.display=A.thinkingDisplay" in out
    )


def _original_scope_map(var: str = "R9") -> str:
    entries = ",".join(
        f'["{scope}",{var}({r},{g},{b})]' for scope, r, g, b in _SYNTAX_DARK_MAP
    )
    return f"new Map([{entries}])"


def test_catppuccin_recolors_full_map() -> None:
    out = CATPPUCCIN_SYNTAX.apply("prefix=" + _original_scope_map() + ";suffix")
    assert "R9(198,160,246)" in out  # keyword -> Catppuccin mauve
    assert "R9(249,38,114)" not in out  # Monokai pink gone
    assert '["title.function",R9(' in out  # every entry kept (not dropped)


def test_version_gating_skips_below_2_1_151() -> None:
    assert not CHANNELS_ENABLED.applies_to((2, 1, 150))
    assert CHANNELS_ENABLED.applies_to((2, 1, 151))
    assert CATPPUCCIN_SYNTAX.applies_to((2, 1, 170))


def test_kimi_brand_relabels_startup_title() -> None:
    out = PatchSet(name="l", patches=(_startup_label_patch("Kimi Code"),)).apply(
        _LABEL_SRC
    )
    assert out == 'l=w1.createElement(y,{bold:!0},"Kimi Code")'


@pytest.mark.parametrize(
    "attribution",
    (
        'k_H(H)!==null?Tw6(H):"Claude Fable 5"',
        'f5H(H)!==null?_j6(H):"Claude"',
        'PyH(H)?IK8(bDH.firstParty):fU4(H)?IK8(H):"Claude"',
    ),
)
def test_attribution_maps_runtime_model(attribution: str) -> None:
    src = (
        f"let H=w7(),$={attribution},"
        'q={FABLE_ID:"claude-fable-5",FABLE_NAME:"Claude Fable 5"}'
    )
    out = PatchSet(
        name="a",
        patches=(
            _attribution_patch({"glm-5.2": "GLM 5.2", "glm-5-turbo": "GLM 5 Turbo"}),
        ),
    ).apply(src)
    # The co-author uses a lowercased runtime model ID lookup with a raw-ID fallback.
    assert '$=({"glm-5.2":"GLM 5.2","glm-5-turbo":"GLM 5 Turbo"})[' in out
    assert "[(''+H).toLowerCase()]??H," in out
    assert attribution not in out
    assert 'FABLE_NAME:"Claude Fable 5"' in out


def test_attribution_required_no_op_fails() -> None:
    with pytest.raises(PatchError, match="patch 'attribution-model' matched nothing"):
        PatchSet(name="a", patches=(_attribution_patch({"gpt-5.6": "GPT-5.6"}),)).apply(
            'let H=w7(),$="Claude"'
        )


def test_identity_and_email_rebrand() -> None:
    src = (
        'a="You are Claude Code, Anthropic\'s official CLI for Claude.";'
        'b="Co-Authored-By: x <noreply@anthropic.com>"'
    )
    out = PatchSet(
        name="ie", patches=(_identity_patch("GLM 5.2"), _email_patch("z.ai"))
    ).apply(src)
    assert (
        "You are GLM 5.2 running in Claude Code, Anthropic's official CLI for Claude"
        in out
    )
    assert "noreply@z.ai" in out
    assert "noreply@anthropic.com" not in out


def test_skip_onboarding_neutralizes_first_run() -> None:
    out = PatchSet(name="o", patches=(_SKIP_ONBOARDING,)).apply(_ENTRY_SRC)
    assert "!1||(process.env.CLAUDE_CODE_TEAM_ONBOARDING" in out
    assert "hasCompletedOnboarding||(process.env" not in out  # first-run gate gone
    assert 'CLAUDE_CODE_TEAM_ONBOARDING==="step"' in out  # env override preserved


def test_model_costs_prepends_provider_models() -> None:
    src = "},Xz_=v8H;ej$={[sJ(FY6.firstParty)]:Xw6,[sJ(UY6.firstParty)]:V_H}"
    out = PatchSet(
        name="c",
        patches=(
            _model_costs_patch(
                {
                    "gpt-5.5": {
                        "inputTokens": 5,
                        "outputTokens": 30,
                        "promptCacheWriteTokens": 0,
                        "promptCacheReadTokens": 0.5,
                        "webSearchRequests": 0.01,
                    },
                }
            ),
        ),
    ).apply(src)
    assert 'ej$={"gpt-5.5":{inputTokens:5,outputTokens:30,' in out
    assert "promptCacheWriteTokens:0,promptCacheReadTokens:0.5" in out
    assert ',[sJ(FY6.firstParty)]:Xw6' in out


def test_splash_injects_on_interactive_tty() -> None:
    out = PatchSet(name="s", patches=(_splash_patch("\x1b[1mHI\x1b[0m\n"),)).apply(
        _ENTRY_SRC
    )
    assert (
        "mcpApprovalSkipWarning:A};if(process.stdout.isTTY)process.stdout.write(" in out
    )
    assert "\\u001b[1mHI" in out  # ANSI escaped into the JS string literal
    assert "let z=S$(),Y=!1;" in out  # original code preserved after the inject


def test_splash_patch_appends_trailing_newline() -> None:
    out = PatchSet(name="s", patches=(_splash_patch("HI"),)).apply(_ENTRY_SRC)
    assert 'process.stdout.write("HI\\n")' in out


def test_brand_patch_sets_dispatch() -> None:
    assert brand_patch_sets(None) == []
    assert len(brand_patch_sets("kimi")) == 1
    assert len(brand_patch_sets("zai")) == 1
    assert len(brand_patch_sets("minimax")) == 1
    assert len(brand_patch_sets("openai")) == 1
    # label + 3 verb/symbol + attribution + identity + email + model-costs
    # + onboarding = 9; passing splash adds the startup-splash patch.
    assert len(brand_patch_sets("kimi")[0].patches) == 9
    assert len(brand_patch_sets("kimi", "ART")[0].patches) == 10
    with pytest.raises(PatchError, match="unknown brand"):
        brand_patch_sets("nope")


def test_kimi_verbs_and_symbols() -> None:
    present = "[" + ",".join(f'"Word{i}ing"' for i in range(55)) + "]"
    past = "[" + ",".join(f'"Word{i}ed"' for i in range(8)) + "]"
    symbols = r'["\xB7","✢","✶","*"]'  # escaped, like the real binary
    src = f"a={present};b={past};c={symbols};"

    out = PatchSet(
        name="vs", patches=_verb_symbol_patches(_KIMI_VERBS, _KIMI_SYMBOLS)
    ).apply(src)

    assert '"Sparking","Glinting"' in out  # present tense
    assert '"Sparked","Glinted"' in out  # ing -> ed
    assert '["·","•","◦","•"]' in out  # spinner glyphs
    assert "Word0ing" not in out  # defaults replaced


# Real 2.1.170 shape: the TodoWrite build tail (todo-schema before/after strings +
# the tool constructor), the tool-registry function head, and the auto-compact
# verdict — the three compact_session anchors in one synthetic snippet (no binary
# needed). Every identifier here is minifier-assigned and differs per platform, so
# the snippet is rendered for each build's real IDs (captured from the linux-x64 and
# darwin-arm64 2.1.170 bundles) to prove the anchors derive them structurally rather
# than by literal.
class _CompactFlavor(NamedTuple):
    label: str
    schema_ns: str  # zod-like namespace (N on linux-x64, k on darwin-arm64)
    describe: str  # todo-item schema helper (ZK$ / W9_)
    builder: str  # tool constructor (aK / a9)
    todo_tool: str  # TodoWrite tool var (JZH / M0H)
    registry_fn: str  # tool-registry function (qg / TQ)
    tool0: str  # first tool in the registry array (yh8 / Cv6)
    tool1: str  # second tool (KS8 / AE6)
    verdict: str  # auto-compact verdict var (A / z)


_COMPACT_FLAVORS = (
    _CompactFlavor("linux-x64", "N", "ZK$", "aK", "JZH", "qg", "yh8", "KS8", "A"),
    _CompactFlavor("darwin-arm64", "k", "W9_", "a9", "M0H", "TQ", "Cv6", "AE6", "z"),
)


def _compact_src(f: _CompactFlavor) -> str:
    ns, dsc, bld, tool = f.schema_ns, f.describe, f.builder, f.todo_tool
    return (
        "_x=yH(()=>"
        + ns
        + ".object({oldTodos:"
        + dsc
        + '().describe("The todo list before the update"),newTodos:'
        + dsc
        + '().describe("The todo list after the update")})),'
        + tool
        + "="
        + bld
        + "({name:wk,async description(){return e24},"
        "get inputSchema(){return _x()},async call({todos:H},$){return{data:{}}}});"
        "function "
        + f.registry_fn
        + "(){return["
        + f.tool0
        + ","
        + f.tool1
        + ",vD,RB,eb,"
        + tool
        + ",...cS4?[cS4]:[]]}"
        # Real xXf guard chain: the querySource + autocompact-enabled guards, then the
        # source=="auto" skip guard (Ue()&&!ni()&&!X4$($,q)) that returns BEFORE the
        # verdict line -- so the flag must be consumed ahead of it, not at the verdict.
        "function xXf(H,$,q,K,_=0){"
        'if(K==="compact")return!1;if(!aT())return!1;'
        "if(Ue()&&!ni()&&!X4$($,q))return!1;let g=PX(H)-_,"
        + f.verdict
        + "=FuH(g,$,q);return k(`autocompact`),"
        + f.verdict
        + '.level==="compact"||'
        + f.verdict
        + '.level==="blocked"}'
    )


@pytest.mark.parametrize(
    "f", _COMPACT_FLAVORS, ids=[fl.label for fl in _COMPACT_FLAVORS]
)
def test_compact_session_applies(f: _CompactFlavor) -> None:
    out = COMPACT_SESSION.apply(_compact_src(f))
    # tool defined with THIS build's constructor and schema namespace, not a literal
    assert f'globalThis.__ccCompactTool={f.builder}({{name:"compact_session"' in out
    assert f"get inputSchema(){{return {f.schema_ns}.object({{}})}}" in out
    # exposes prompt()/searchHint like every real tool, so API tool serialization
    # (gtf -> H.prompt) doesn't throw `H.prompt is not a function` on every request
    assert '"compact_session",searchHint:"' in out
    assert 'async prompt(){return"Schedule compaction' in out
    # result mapping reads the .data payload the framework passes (map(t.data,id)),
    # i.e. H.message -- not H.data.message (that double-dip threw at call time)
    assert 'content:H.message}' in out
    assert "content:H.data.message" not in out
    # the generic tool-use renderer calls renderToolUseMessage() unconditionally on
    # render; it is not an aK/base default, so omitting it throws `undefined(...)`
    assert "renderToolUseMessage(){return null}" in out
    # registered at the head of the registry array, before its original first tools
    assert (
        f"function {f.registry_fn}(){{return"
        "[...(globalThis.__ccCompactTool?[globalThis.__ccCompactTool]:[]),"
        f"{f.tool0},{f.tool1}," in out
    )
    # consumes the pending-compact flag BEFORE the source=="auto" skip guard, so an
    # explicit compact_session forces compaction even on models that skip proactive
    # autocompact (opus-4-8 1M window, haiku absent from SXf); the untouched guard
    # (and verdict line) still drive the normal token-threshold path afterwards
    assert (
        "if(globalThis.__ccPendingCompact)"
        "return globalThis.__ccPendingCompact=!1,!0;"
        "if(Ue()&&!ni()&&!X4$($,q))return!1" in out
    )
    assert f'{f.verdict}.level==="compact"||{f.verdict}.level==="blocked"' in out
    # the TodoWrite build is preserved immediately after the injected tool
    assert f",{f.todo_tool}={f.builder}({{name:wk" in out


# Members the 2.1.170 harness reads/calls UNCONDITIONALLY on a tool object and that
# the aK/base defaults do NOT provide -- so the injected tool must define each or it
# TypeErrors at invoke/render time (which apply()+verify, a static string transform,
# cannot see). Derived from cli.js: gtf reads prompt()/searchHint per request; the
# generic tool-use renderer calls renderToolUseMessage(); plus call, result mapping,
# and the input schema. Each earlier gap here was a shipped runtime bug
# (be69994 prompt, 3300266 mapping, this one renderToolUseMessage).
_COMPACT_TOOL_REQUIRED_MEMBERS = (
    "searchHint:",
    "async description(",
    "async prompt(",
    "get inputSchema(",
    "renderToolUseMessage(",
    "async call(",
    "mapToolResultToToolResultBlockParam(",
)


@pytest.mark.parametrize(
    "f", _COMPACT_FLAVORS, ids=[fl.label for fl in _COMPACT_FLAVORS]
)
def test_compact_session_tool_shape_complete(f: _CompactFlavor) -> None:
    out = COMPACT_SESSION.apply(_compact_src(f))
    start = out.index(f"globalThis.__ccCompactTool={f.builder}(")
    tool = out[start : out.index(f",{f.todo_tool}={f.builder}(", start)]
    for member in _COMPACT_TOOL_REQUIRED_MEMBERS:
        assert member in tool, f"injected compact_session tool missing {member!r}"


@pytest.mark.parametrize(
    "f", _COMPACT_FLAVORS, ids=[fl.label for fl in _COMPACT_FLAVORS]
)
def test_compact_session_idempotent(f: _CompactFlavor) -> None:
    once = COMPACT_SESSION.apply(_compact_src(f))
    # re-match-proofed: on a second apply the define patch matches nothing and the
    # required-match guard raises, so no double-injection is possible.
    with pytest.raises(PatchError, match="define-compact-session-tool"):
        COMPACT_SESSION.apply(once)
