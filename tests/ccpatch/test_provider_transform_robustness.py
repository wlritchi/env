"""Check exact edits inside matched provider fragments."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from wlrenv.ccpatch.patches import BACKGROUND_PROVIDER_ENV, PatchError

_CASES = [
    (
        "add-control-provider-env",
        'z.object({proto:p,op:z.literal("dispatch"),d:job(),timeoutMs:z.number(),auth:',
        'timeoutMs:',
    ),
    (
        "acknowledge-provider-env-version",
        'return respond(socket,{ok:!0,op:operation,short:id,pid:worker.record.pid,',
        'op:operation,',
    ),
    (
        "declare-worker-provider-env",
        'Worker{dispatch;spawnPty;getAuthSnapshot;via;record;',
        'dispatch;',
    ),
    (
        "thread-provider-env-through-spawn",
        'static spawn(job,spawn,auth,options){let worker=new Worker(job,spawn??fallback(),auth,"cold");',
        '"cold")',
    ),
    (
        "apply-provider-env-to-claim-frame",
        'static buildClaimFrame(job,snapshot,auth){let dir=jobDir(job.short),env=buildEnv(job,dir,snapshot,sock(job.short),auth);',
        'auth)',
    ),
    (
        "thread-provider-env-to-claim-frame",
        'function frame(job,snapshot,auth,claimAuth){let{env:e,argv:a}=Worker.buildClaimFrame(job,snapshot,auth);',
        'auth);',
    ),
    (
        "accept-transient-provider-env-in-manager",
        'dispatch=async(job,retry=0,afterUpgrade)=>{',
        'afterUpgrade)=>',
    ),
    (
        "remove-provider-env-from-state-writes",
        'async function write(dir,state){let{pinned:p,sortOrder:s,stateSortOrder:i,...rest}=state;',
        '...rest',
    ),
    (
        "scrub-worker-provider-env",
        'function build(job,dir,snapshot,sock,auth){let ambient={...process.env},env={...ambient,OTHER:"kept"};if(process.env.',
        'auth){',
    ),
]


@pytest.mark.parametrize(("name", "source", "anchor"), _CASES)
def test_provider_exact_edits_reject_drift(
    name: str, source: str, anchor: str, mocker: MockerFixture
) -> None:
    patch = next(
        patch for patch in BACKGROUND_PROVIDER_ENV.patches if patch.name == name
    )
    match = patch.pattern.fullmatch(source)
    assert match is not None
    assert not isinstance(patch.replacement, str)
    assert patch.replacement(match) != source

    for altered in (
        source.replace(anchor, "missingAnchor"),
        source.replace(anchor, anchor + anchor, 1),
    ):
        changed = mocker.MagicMock(spec=re.Match)

        def group(key: str | int, text: str = altered) -> str | None:
            return text if key == 0 else match.group(key)

        changed.group.side_effect = group
        with pytest.raises(PatchError, match="provider"):
            patch.replacement(changed)


def test_claim_frame_changes_signature_and_call_exactly_once() -> None:
    name, source, _ = _CASES[4]
    patch = next(
        patch for patch in BACKGROUND_PROVIDER_ENV.patches if patch.name == name
    )
    patched, count = patch.pattern.subn(patch.replacement, source)
    assert count == 1
    assert patched == source.replace('auth)', 'auth,_ccProviderEnv)')
    assert patched.count('_ccProviderEnv') == 2


def test_claimed_entry_preserves_incidental_braces() -> None:
    source = (
        'async function entry(claim,main){let extra={nested:{keep:!0}};'
        'Object.assign(process.env,claim.env),process.argv=["node"],'
        'cache(),auth(),other();let{main:workerMain}=await main;await workerMain()}'
    )
    patch = next(
        patch
        for patch in BACKGROUND_PROVIDER_ENV.patches
        if patch.name == 'apply-provider-env-after-claimed-initializers'
    )
    patched, count = patch.pattern.subn(patch.replacement, source)
    assert count == 1
    assert patched == source.replace(
        'entry(claim,main){',
        'entry(claim,main){_ccProviderWorkerEnv=_ccProviderCaptureTransport(claim.env);',
    ).replace('await workerMain()', '_ccProviderApplyWorkerFinal();await workerMain()')


_RV_GUARD = (
    'if(token&&!authenticated&&request.type!=="repaint"){'
    'send({type:"reply-rejected"});return}'
)
_RV_SHUTDOWN = 'if(request.type==="shutdown"){'
_RV_EXTRACTED = (
    'shutdown();return}'
    'if(request.type==="repaint"){repaint();return}'
    'if(request.type==="attacher-caps"){caps(request);return}'
    'if(request.type==="reply"&&typeof request.text==="string")reply(request)}'
    'function shutdown(){'
)
_RV_SEND = 'send({type:"shutting-down"});'
_RV_DISCOVERY = (
    'const metadata={sessionId:session(),gates:{}};'
    'const supervisor={proto:protocol,role:"supervisor",supervisorPid:1};'
    'function authenticate(request){if("auth"in request&&validate(request.auth,token))'
    'authenticated=!0;return}'
)


@pytest.mark.parametrize('extracted', [False, True], ids=['legacy', '207'])
def test_authenticated_transfer_preserves_dispatch_and_checks_auth(
    extracted: bool,
) -> None:
    patch = next(
        patch
        for patch in BACKGROUND_PROVIDER_ENV.patches
        if patch.name == 'serve-authenticated-provider-snapshot-on-worker-rv'
    )
    fragment = (
        _RV_GUARD + _RV_SHUTDOWN + (_RV_EXTRACTED if extracted else '') + _RV_SEND
    )
    source = _RV_DISCOVERY + 'function dispatch(request){' + fragment + '}}'
    if extracted:
        source = source[:-1]
    patched, count = patch.pattern.subn(patch.replacement, source)
    assert count == 1
    start = patched.index('if(request.type==="cc-provider-snapshot-request")')
    end = patched.index(_RV_SHUTDOWN, start)
    assert patched[:start] + patched[end:] == source
    assert patched.index(_RV_GUARD) < start < end
    assert patched.count('_ccReply.mac=_ccProviderMac(token,_ccReply)') == 1
    node = shutil.which('node')
    if node is None:
        pytest.skip('node is not available')
    program = (
        'let token="secret",authenticated=true,protocol=7,outputs=[];'
        'const session=()=>"session",validate=(a,b)=>a===b,send=x=>outputs.push(x);'
        'const _ccProviderWorkerEnv={API_KEY:"private"},_ccProviderMac=()=>"mac";'
        + patched
        + ';const valid={type:"cc-provider-snapshot-request",auth:token,proto:7,'
        'version:3,sessionId:"session",nonce:"a".repeat(64)};'
        'dispatch(valid);if(outputs.length!==1||outputs[0].mac!=="mac"||'
        'outputs[0].payload.API_KEY!=="private")throw Error("missing snapshot");'
        'for(const invalid of [{auth:"bad"},{proto:8},{version:2},'
        '{sessionId:"other"},{nonce:"bad"}]){outputs=[];dispatch({...valid,...invalid});'
        'if(outputs.length)throw Error("invalid request leaked snapshot")}'
        'for(const state of [["secret",false],["",true],[null,true]]){'
        '[token,authenticated]=state;outputs=[];dispatch(valid);'
        'if(outputs.some(x=>x.type==="cc-provider-snapshot"))throw Error("auth bypass")}'
        'token="secret";authenticated=true;outputs=[];dispatch({type:"shutdown"});'
        'if(outputs[0].type!=="shutting-down")throw Error("shutdown changed");'
    )
    subprocess.run(  # noqa: S603 - Execute the synthetic request dispatcher.
        [node, '-e', program], check=True, capture_output=True, text=True
    )


@pytest.mark.parametrize(
    'change',
    [
        ('function shutdown()', 'function unrelated()'),
        ('shutdown();return}', 'shutdown(request);return}'),
        ('type==="attacher-caps"', 'type==="unknown"'),
    ],
)
def test_authenticated_transfer_rejects_extracted_shutdown_drift(
    change: tuple[str, str],
) -> None:
    patch = next(
        patch
        for patch in BACKGROUND_PROVIDER_ENV.patches
        if patch.name == 'serve-authenticated-provider-snapshot-on-worker-rv'
    )
    source = _RV_GUARD + _RV_SHUTDOWN + _RV_EXTRACTED + _RV_SEND
    assert len(list(patch.pattern.finditer(source))) == 1
    assert patch.pattern.search(source.replace(*change)) is None


@pytest.mark.parametrize(
    'platform', ['linux-x64', 'linux-arm64', 'darwin-x64', 'darwin-arm64']
)
def test_authenticated_transfer_207_platform_source(platform: str) -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / 'build/sweep-resume/2.1.207'
        / platform
        / 'original.js'
    )
    if not path.is_file():
        pytest.skip('local .207 source is not available')
    source = path.read_text()
    patch = next(
        patch
        for patch in BACKGROUND_PROVIDER_ENV.patches
        if patch.name == 'serve-authenticated-provider-snapshot-on-worker-rv'
    )
    matches = list(patch.pattern.finditer(source))
    assert len(matches) == 1
    match = matches[0]
    assert match.group('shutdown') is not None
    patched, count = patch.pattern.subn(patch.replacement, source)
    assert count == 1
    assert patched.count('type==="cc-provider-snapshot-request"') == 1
    assert patched.count('payload:_ccProviderWorkerEnv') == 1
    assert f'{match.group("send")}(_ccReply)' in patched
    assert (
        f'function {match.group("shutdown")}(){{{match.group("send")}({{type:"shutting-down"}});'
        in patched
    )
