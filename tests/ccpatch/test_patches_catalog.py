"""Tests for the ported patch catalog (Fable, channels, dev-channel, syntax).

Each uses a synthetic snippet matching the real 2.1.170 minification so the
patches are covered without a binary. End-to-end application against a real
binary is exercised in test_elf_integration / manual validation.
"""

from __future__ import annotations

import pytest

from wlrenv.ccpatch.patches import (
    _KIMI_SYMBOLS,
    _KIMI_VERBS,
    _SKIP_ONBOARDING,
    _SYNTAX_DARK_MAP,
    CATPPUCCIN_SYNTAX,
    CHANNELS_ENABLED,
    DEV_CHANNEL_INHERITANCE,
    FABLE_MODEL,
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


def test_enfable_inserts_push() -> None:
    src = "let{availableModels:q}=$;if(!q)return!0;if(q.length===0)return!1;rest"
    out = FABLE_MODEL.apply(src)
    assert "if(!q)return!0;q.push('fable');if(q.length===0)return!1;" in out


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
    assert not FABLE_MODEL.applies_to((2, 1, 150))
    assert FABLE_MODEL.applies_to((2, 1, 151))
    assert CATPPUCCIN_SYNTAX.applies_to((2, 1, 170))


def test_kimi_brand_relabels_startup_title() -> None:
    out = PatchSet(name="l", patches=(_startup_label_patch("Kimi Code"),)).apply(
        _LABEL_SRC
    )
    assert out == 'l=w1.createElement(y,{bold:!0},"Kimi Code")'


def test_attribution_maps_runtime_model() -> None:
    src = (
        'let H=w7(),$=k_H(H)!==null?Tw6(H):"Claude Fable 5",'
        'q={FABLE_ID:"claude-fable-5",FABLE_NAME:"Claude Fable 5"}'
    )
    out = PatchSet(
        name="a",
        patches=(
            _attribution_patch({"glm-5.2": "GLM 5.2", "glm-5-turbo": "GLM 5 Turbo"}),
        ),
    ).apply(src)
    # co-author looks the runtime model id up in the map, lowercased, with fallback
    assert '$=({"glm-5.2":"GLM 5.2","glm-5-turbo":"GLM 5 Turbo"})[' in out
    assert "[(''+H).toLowerCase()]??H," in out
    assert '?Tw6(H):"Claude Fable 5"' not in out  # ternary gone
    assert 'FABLE_NAME:"Claude Fable 5"' in out  # the Fable model constant is untouched


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
    # label + 3 verb/symbol + attribution + identity + email + onboarding = 8;
    # passing splash adds the startup-splash patch.
    assert len(brand_patch_sets("kimi")[0].patches) == 8
    assert len(brand_patch_sets("kimi", "ART")[0].patches) == 9
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
