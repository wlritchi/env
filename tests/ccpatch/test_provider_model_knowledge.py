"""Keep provider metadata visible to the native unknown-model guard."""

import json
import shutil
import subprocess

import pytest

from wlrenv.ccpatch.patches import (
    _MULTI_PROVIDER_CATALOG,
    _MULTI_PROVIDER_HELPER,
    PatchError,
    _recognize_provider_catalog,
)

_REGISTRATION = (
    "register({isKnown:(e)=>known(identity(e,{identity:!0})),"
    "isModelId:(e)=>lookup(e)!==void 0,isClientSpelling:spelling});"
)
_GUARD = "CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT"


def test_provider_catalog_extends_native_knowledge() -> None:
    runtime = shutil.which("node") or shutil.which("bun")
    if runtime is None:
        pytest.skip("node/bun is required to evaluate model knowledge")
    patched = _recognize_provider_catalog(_REGISTRATION)
    script = (
        'const assert=require("node:assert/strict");let knowledge,nativeCalls=0;'
        "const register=e=>knowledge=e,identity=e=>e,lookup=()=>undefined,spelling=()=>false;"
        'const known=e=>{nativeCalls++;return e==="claude-fable-5"};'
        + _MULTI_PROVIDER_HELPER
        + patched
        + f"const catalog={_MULTI_PROVIDER_CATALOG};"
        + "for(const row of catalog)assert.equal(knowledge.isKnown(row.value),true);"
        + 'assert.equal(knowledge.isKnown("kimi:kimi-k3"),true);'
        + "assert.equal(nativeCalls,0);"
        + 'assert.equal(knowledge.isKnown("claude-fable-5"),true);'
        + 'for(const model of ["openai:future","gpt-6-astra","unknown",'
        + '"OPENAI:gpt-6-astra","openai:gpt-6-astra[1m]"])'
        + "assert.equal(knowledge.isKnown(model),false);"
        + 'assert.equal(_ccMultiProviderCatalogInfo("openai:gpt-6-astra").contextWindow,272000);'
        + "assert.equal(nativeCalls,6);"
    )
    subprocess.run(  # noqa: S603
        [runtime, "-e", script], check=True, capture_output=True, text=True
    )


def test_older_build_without_knowledge_registry_is_unchanged() -> None:
    source = "function nativeModel(e){return e}"
    assert _recognize_provider_catalog(source) == source


@pytest.mark.parametrize(
    "registration",
    [
        "",
        _REGISTRATION.replace("isKnown:", "isRecognized:"),
        _REGISTRATION.replace("identity:!0", "identity:!1"),
        _REGISTRATION + _REGISTRATION,
    ],
)
def test_unknown_window_guard_fails_closed_if_registration_changes(
    registration: str,
) -> None:
    source = f"const guard={json.dumps(_GUARD)};{registration}"
    with pytest.raises(PatchError, match="model knowledge registration"):
        _recognize_provider_catalog(source)


def test_provider_registration_is_not_applied_twice() -> None:
    source = f"const guard={json.dumps(_GUARD)};{_REGISTRATION}"
    with pytest.raises(PatchError, match="model knowledge registration"):
        _recognize_provider_catalog(_recognize_provider_catalog(source))
