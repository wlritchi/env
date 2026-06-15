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
    _SYNTAX_DARK_MAP,
    CATPPUCCIN_SYNTAX,
    CHANNELS_ENABLED,
    DEV_CHANNEL_INHERITANCE,
    FABLE_MODEL,
    PatchError,
    PatchSet,
    _startup_label_patch,
    _verb_symbol_patches,
    brand_patch_sets,
)

# Real 2.1.170 startup-title anchor.
_LABEL_SRC = (
    'l=eu8?w1.createElement(eu8.Title,null):w1.createElement(y,{bold:!0},"Claude Code")'
)

_DEV_CHANNEL_SRC = (
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


def test_dev_channel_inheritance_injects_bg_handler() -> None:
    out = DEV_CHANNEL_INHERITANCE.apply(_DEV_CHANNEL_SRC)
    assert 'CLAUDE_CODE_SESSION_KIND==="bg"){let devChannelsEnv=' in out
    # registrar / base / parse identifiers reused from the captured block
    assert "if(devChannelsEnv)n9H([...r$,...c$(devChannelsEnv.split(" in out
    # the original telemetry block is preserved (length-free append)
    assert 'd("tengu_mcp_channel_flags",{})' in out


def test_dev_channel_required_no_op_fails() -> None:
    with pytest.raises(PatchError, match="dev-channel-inheritance"):
        DEV_CHANNEL_INHERITANCE.apply("unrelated source")


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


def test_brand_patch_sets_dispatch() -> None:
    assert brand_patch_sets(None) == []
    assert len(brand_patch_sets("kimi")) == 1
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
