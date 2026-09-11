"""Check exact edits inside matched provider fragments."""

import re

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
