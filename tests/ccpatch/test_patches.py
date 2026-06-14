"""Tests for the length-free JS patch framework and the thinking patch set."""

from __future__ import annotations

import re

import pytest

from wlrenv.ccpatch.patches import (
    Patch,
    PatchError,
    PatchSet,
    parse_version,
    thinking_expanded,
)

# A minified-ish snippet exercising all three thinking patches.
_RENDER = (
    'case"thinking":{if(!Ab&&!Cd)return null;'
    "return q.createElement(Xy,{addMargin:Mn,param:Pq,"
    "isTranscriptMode:Rs,verbose:Tu})}"
)
_GROUP = "a:null,fG=Oh===null?ob_(Yz):void 0;if(Kw){Kw.latestThinkingSummary=x"
_SOURCE = _RENDER + ";" + _GROUP


def test_parse_version() -> None:
    assert parse_version("2.1.170") == (2, 1, 170)
    assert parse_version("2.1.170-beta.1") == (2, 1, 170)


def test_thinking_render_patches_apply() -> None:
    out = thinking_expanded((2, 1, 170)).apply(_SOURCE)
    assert "isTranscriptMode:true,verbose:true" in out
    assert "return null;" not in out.split("verbose")[0]  # early guard gone
    assert "===null?void 0:void 0" in out  # grouping neutralized


def test_version_gating_excludes_ungroup_below_2_1_151() -> None:
    # Below 2.1.151 the grouping patch is not included, so a source WITHOUT the
    # grouping pattern still applies cleanly (no required no-op failure).
    out = thinking_expanded((2, 1, 150)).apply(_RENDER)
    assert "isTranscriptMode:true,verbose:true" in out


def test_ungroup_required_above_2_1_151_fails_when_absent() -> None:
    with pytest.raises(PatchError, match="disable-thinking-grouping"):
        thinking_expanded((2, 1, 170)).apply(_RENDER)  # no grouping pattern present


def test_verify_absent_catches_silent_failure() -> None:
    # A patch that does nothing but claims to remove the guard must fail verify.
    bogus = PatchSet(
        name="bogus",
        patches=(
            Patch(
                name="noop", pattern=re.compile("ZZZ"), replacement="", required=False
            ),
        ),
        verify_absent=(re.compile("return null;"),),
    )
    with pytest.raises(PatchError, match="forbidden marker"):
        bogus.apply(_SOURCE)


def test_applies_to_version_bounds() -> None:
    ps = PatchSet(
        name="x", patches=(), min_version=(2, 1, 100), max_version=(2, 1, 200)
    )
    assert ps.applies_to((2, 1, 150))
    assert not ps.applies_to((2, 1, 99))
    assert not ps.applies_to((2, 1, 200))
    assert ps.applies_to(None)
