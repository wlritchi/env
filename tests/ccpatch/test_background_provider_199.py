"""Check transient provider data at the 2.1.199 state-write boundary."""

import shutil
import subprocess

import pytest

from wlrenv.ccpatch.patches import BACKGROUND_PROVIDER_ENV_198

_STATE_PREFIX = (
    'async function write(dir,state){let '
    'cron=state.inFlight?.kinds.includes("session_cron")===!0,'
    'normalized=cron&&!state.selfWake?{...state,selfWake:!0}:'
    '!cron&&state.selfWake&&canClear(state)?{...state,selfWake:void 0}:state,'
    '{pinned:p,sortOrder:s,stateSortOrder:i,group:g,...rest}=normalized;'
)


def test_199_state_write_removes_credentials_after_normalization() -> None:
    patch = next(
        patch
        for patch in BACKGROUND_PROVIDER_ENV_198.patches
        if patch.name == "remove-provider-env-from-state-writes"
    )
    patched, count = patch.pattern.subn(patch.replacement, _STATE_PREFIX)
    assert count == 1
    assert patched == _STATE_PREFIX.replace(
        "...rest}=normalized", "providerEnv:_ccProviderEnv,...rest}=normalized"
    )
    runtime = shutil.which("node") or shutil.which("bun")
    assert runtime is not None
    script = (
        'const canClear=state=>state.clear;'
        + patched
        + 'return JSON.parse(JSON.stringify(rest))}'
        + '''
(async()=>{
    for(const cron of [false,true])
    for(const selfWake of [false,true])
    for(const clear of [false,true]){
        const state={inFlight:{kinds:cron?["session_cron"]:[]},selfWake,clear,
            providerEnv:{ANTHROPIC_API_KEY:"secret",ANTHROPIC_AUTH_TOKEN:"token",
                CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST:"1"},
            pinned:true,sortOrder:1,stateSortOrder:2,group:"group",name:"kept"};
        const result=await write("unused",state);
        for(const key of ["providerEnv","pinned","sortOrder","stateSortOrder","group"])
            if(key in result)throw Error("persisted "+key);
        if(JSON.stringify(result).includes("secret"))throw Error("persisted credential");
        const expected=cron?true:selfWake&&clear?undefined:selfWake;
        if(result.selfWake!==expected||result.name!=="kept")throw Error("state changed");
        if(state.providerEnv.ANTHROPIC_AUTH_TOKEN!=="token")throw Error("input changed");
    }
})().catch(error=>{console.error(error);process.exitCode=1});
'''
    )
    result = subprocess.run(  # noqa: S603 - runtime is a resolved node/bun executable
        [runtime, "-e", script], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "source",
    [
        _STATE_PREFIX.replace("...rest}=normalized", "...rest}=state"),
        _STATE_PREFIX.replace("group:g,", ""),
        _STATE_PREFIX.replace('"session_cron"', '"other_cron"'),
    ],
)
def test_199_state_write_rejects_unrecognized_layout(source: str) -> None:
    patch = next(
        patch
        for patch in BACKGROUND_PROVIDER_ENV_198.patches
        if patch.name == "remove-provider-env-from-state-writes"
    )
    assert patch.pattern.search(source) is None
