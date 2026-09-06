"""Tests for the length-free JS patch framework and the thinking patch set."""

from __future__ import annotations

import re

import pytest

from wlrenv.ccpatch.patches import (
    _PROVIDER_ENV_RESPAWN_GUARD,
    CHANNELS_ENABLED,
    Patch,
    PatchError,
    PatchSet,
    discover_identifiers,
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


@pytest.mark.parametrize(
    ("source", "patterns", "error"),
    [
        ("unrelated", (r"anchor=(?P<name>[\w$]+)",), "expected one match, got 0"),
        (
            "anchor=$x;anchor=$x",
            (r"anchor=(?P<name>[\w$]+)",),
            "expected one match, got 2",
        ),
        ("anchor=", (r"anchor=(?P<name>[\w$]+)?",), "missing binding 'name'"),
        (
            "a=$x;b=$y",
            (r"a=(?P<name>[\w$]+)", r"b=(?P<name>[\w$]+)"),
            "conflicting binding 'name'",
        ),
    ],
)
def test_identifier_discovery_rejects_invalid_anchors(
    source: str, patterns: tuple[str, ...], error: str
) -> None:
    with pytest.raises(PatchError, match=error):
        discover_identifiers(source, tuple(re.compile(pattern) for pattern in patterns))


def test_identifier_discovery_merges_consistent_bindings() -> None:
    assert discover_identifiers(
        "a=$x;b=$x;c=Y$",
        (
            re.compile(r"a=(?P<name>[\w$]+)"),
            re.compile(r"b=(?P<name>[\w$]+);c=(?P<other>[\w$]+)"),
        ),
    ) == {"name": "$x", "other": "Y$"}
    assert discover_identifiers("anything", ()) == {}


def test_patch_replacement_receives_discovered_bindings() -> None:
    def replace(match: re.Match[str], bindings: dict[str, str]) -> str:
        return f"{bindings['callee']}({match.group('argument')})"

    patch_set = PatchSet(
        name="bound",
        patches=(
            Patch(
                name="call",
                pattern=re.compile(r"CALL\((?P<argument>[\w$]+)\)"),
                replacement="unused",
                identifiers=(re.compile(r"anchor=(?P<callee>[\w$]+)"),),
                bound_replacement=replace,
            ),
        ),
    )
    assert patch_set.apply("anchor=$fn;CALL(A);CALL(B$)") == "anchor=$fn;$fn(A);$fn(B$)"
    assert patch_set.apply("anchor=other$;CALL(C)") == "anchor=other$;other$(C)"


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


@pytest.mark.parametrize(
    "source",
    [
        'state?.channelsEnabled!==!0;obj.flag("tengu_harbor",!1)',
        'obj.state?.channelsEnabled!==!0;flag("tengu_harbor",!1)',
    ],
)
def test_channels_enabled_rejects_member_expressions(source: str) -> None:
    with pytest.raises(PatchError):
        CHANNELS_ENABLED.apply(source)


@pytest.mark.parametrize("property_name", ["providerEnvironment", "providerEnv$"])
def test_provider_respawn_guard_preserves_other_properties(property_name: str) -> None:
    source = f"if(active||job$.{property_name})respawn()"
    assert _PROVIDER_ENV_RESPAWN_GUARD.sub("", source) == source


def test_provider_respawn_guard_matches_dollar_identifier_exactly() -> None:
    source = "if(active||job$.providerEnv)respawn()"
    match = _PROVIDER_ENV_RESPAWN_GUARD.search(source)
    assert match is not None
    assert match.group("job") == "job$"
    assert _PROVIDER_ENV_RESPAWN_GUARD.sub("", source) == "if(active)respawn()"
