"""Length-free JavaScript patches applied to the extracted ``cli.js`` source.

Unlike whole-binary byte patching, these run on the decoded source string and
may change its length freely -- the container repack absorbs the size change.
Each :class:`PatchSet` is version-gated and self-verifying: after applying, it
asserts that expected markers appear (and forbidden ones do not), so a silent
no-op on a restructured future build fails loudly instead of shipping a binary
that looks patched but isn't.

Patterns match minified identifiers structurally (``[\\w$]+`` capture/backref),
pinning only stable string and property literals -- resilient to reminification.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass

Version = tuple[int, ...]


class PatchError(RuntimeError):
    """A patch failed to match, or post-apply verification failed."""


def parse_version(text: str) -> Version:
    """``"2.1.170"`` -> ``(2, 1, 170)``; a trailing suffix like ``-beta.1`` is dropped."""
    match = re.match(r"\d+(?:\.\d+)*", text)
    return tuple(int(p) for p in match.group(0).split(".")) if match else ()


@dataclass(frozen=True)
class Patch:
    name: str
    pattern: re.Pattern[str]
    replacement: str | Callable[[re.Match[str]], str]
    required: bool = True  # must match at least once


@dataclass(frozen=True)
class PatchSet:
    name: str
    patches: tuple[Patch, ...]
    verify_present: tuple[re.Pattern[str], ...] = ()
    verify_absent: tuple[re.Pattern[str], ...] = ()
    min_version: Version | None = None  # inclusive
    max_version: Version | None = None  # exclusive

    def applies_to(self, version: Version | None) -> bool:
        if version is None:
            return True
        if self.min_version is not None and version < self.min_version:
            return False
        if self.max_version is not None and version >= self.max_version:
            return False
        return True

    def apply(self, source: str) -> str:
        for patch in self.patches:
            source, n = patch.pattern.subn(patch.replacement, source)
            if patch.required and n == 0:
                raise PatchError(f"{self.name}: patch {patch.name!r} matched nothing")
        for marker in self.verify_present:
            if marker.search(source) is None:
                raise PatchError(
                    f"{self.name}: expected marker absent after apply: {marker.pattern!r}"
                )
        for marker in self.verify_absent:
            if marker.search(source) is not None:
                raise PatchError(
                    f"{self.name}: forbidden marker still present: {marker.pattern!r}"
                )
        return source


# --- concrete patch sets ----------------------------------------------------

_ID = r"[\w$]+"
_V_2_1_151 = (2, 1, 151)

# Drop the early-return guard that hides the standalone thinking block, and force
# its render to the expanded (transcript + verbose) branch.
_THINKING_RENDER = (
    Patch(
        name="drop-thinking-early-return",
        pattern=re.compile(
            rf'(case"thinking":\{{)if\(!{_ID}(?:&&!{_ID})+\)return null;'
        ),
        replacement=r"\1",
    ),
    Patch(
        name="force-transcript-and-verbose",
        pattern=re.compile(
            rf"(createElement\({_ID},\{{addMargin:{_ID},param:{_ID},"
            rf"isTranscriptMode:){_ID}(,verbose:){_ID}"
        ),
        replacement=r"\1true\2true",
    ),
)

# 2.1.151+ folds thinking into the tool-use group; neutralize the extractor so
# the thinking message falls through to the standalone render path above.
_THINKING_UNGROUP = Patch(
    name="disable-thinking-grouping",
    pattern=re.compile(
        rf"(:null,{_ID}={_ID}===null\?)({_ID}\([\w$,]*\))"
        rf"(:void 0;if\({_ID}\)\{{{_ID}\.latestThinkingSummary)"
    ),
    replacement=r"\1void 0\3",
)


def thinking_expanded(version: Version | None) -> PatchSet:
    """Always render assistant thinking in full (pre- and post-2.1.151)."""
    patches = _THINKING_RENDER
    if version is None or version >= _V_2_1_151:
        patches = (*patches, _THINKING_UNGROUP)
    return PatchSet(
        name="thinking-expanded",
        patches=patches,
        verify_present=(re.compile(r"isTranscriptMode:true,verbose:true"),),
        verify_absent=(
            re.compile(rf'case"thinking":\{{if\(!{_ID}(?:&&!{_ID})+\)return null;'),
        ),
    )


# --- Fable in the model list (2.1.151+) -------------------------------------


def _enfable_replacement(m: re.Match[str]) -> str:
    # m.1 = "availableModels:VAR}=X;if(!VAR)return!0;", m.2 = VAR, m.3 = guard
    return f"{m.group(1)}{m.group(2)}.push('fable');{m.group(3)}"


FABLE_MODEL = PatchSet(
    name="fable-in-model-list",
    patches=(
        Patch(
            name="enfable",
            pattern=re.compile(
                rf"(availableModels:({_ID})\}}={_ID};if\(!\2\)return!0;)"
                rf"(if\(\2\.length===0\)return!1;)"
            ),
            replacement=_enfable_replacement,
        ),
    ),
    verify_present=(re.compile(r"\.push\('fable'\);if\([\w$]+\.length===0\)"),),
    min_version=_V_2_1_151,
)

# --- enable channels (2.1.151+) ---------------------------------------------

CHANNELS_ENABLED = PatchSet(
    name="channels-enabled",
    patches=(
        # Force the "channels not enabled" subexpression false -> channels on.
        Patch(
            name="channelator",
            pattern=re.compile(rf"{_ID}\?\.channelsEnabled!==!0"),
            replacement="!1",
        ),
        # Force the tengu_harbor feature flag on.
        Patch(
            name="channelizer",
            pattern=re.compile(rf'{_ID}\("tengu_harbor",!1\)'),
            replacement="!!1",
        ),
    ),
    verify_absent=(
        re.compile(r"\?\.channelsEnabled!==!0"),
        re.compile(r'"tengu_harbor",!1'),
    ),
    min_version=_V_2_1_151,
)

# --- dev-channel inheritance for background agents (2.1.151+) ----------------
#
# Channels passed via --dangerously-load-development-channels never reach the
# bg-agent workers the agents view spawns (the dispatch pipeline drops the flag,
# and bg sessions skip dev-channel registration). The claude shim exports the
# specs as CLAUDE_DEV_CHANNELS, which rides along to every worker. This patch
# teaches the worker to honor it: for bg sessions, parse the env var through the
# binary's own channel parser, tag each {dev:!0} (the allowlist-bypass marker),
# and register them via the binary's own registrar onto the existing list.
#
# Length-free: we append the registration after the existing channel-parse block
# (captured verbatim as group 0); the telemetry block that follows is untouched.
# Every minified identifier is captured, so only stable literals are pinned.


def _dev_channel_replacement(m: re.Match[str]) -> str:
    register, base, parse = m.group(4), m.group(2), m.group(3)
    return m.group(0) + (
        'if(process.env.CLAUDE_CODE_SESSION_KIND==="bg"){'
        "let devChannelsEnv=process.env.CLAUDE_DEV_CHANNELS;"
        f"if(devChannelsEnv){register}([...{base},...{parse}"
        '(devChannelsEnv.split(" ").filter(Boolean),'
        '"--dangerously-load-development-channels")'
        ".map((devEntry)=>({...devEntry,dev:!0}))])}"
    )


DEV_CHANNEL_INHERITANCE = PatchSet(
    name="dev-channel-inheritance",
    patches=(
        Patch(
            name="dev-channel-inheritance",
            pattern=re.compile(
                rf"if\(({_ID})&&\1\.length>0\)({_ID})=({_ID})\(\1,\"--channels\"\),"
                rf"({_ID})\(\2\);if\(!{_ID}\)\{{if\(({_ID})&&\5\.length>0\)"
                rf"{_ID}=\3\(\5,\"--dangerously-load-development-channels\"\)\}}"
            ),
            replacement=_dev_channel_replacement,
        ),
    ),
    verify_present=(
        re.compile(r'CLAUDE_CODE_SESSION_KIND==="bg"\)\{let devChannelsEnv='),
    ),
    min_version=_V_2_1_151,
)

# --- Catppuccin Macchiato syntax highlighting (2.1.151+) ---------------------
#
# The dark "Monokai" token scope map is a hardcoded `new Map([["keyword",...]])`
# literal not reachable via the theme overrides system. Recolor it to Catppuccin
# Macchiato. Length-free: no padding, and we keep every entry (the byte-patch
# version dropped `title.function` only to fit the original byte budget).
_SYNTAX_DARK_MAP: tuple[tuple[str, int, int, int], ...] = (
    ("keyword", 249, 38, 114),
    ("_storage", 102, 217, 239),
    ("built_in", 166, 226, 46),
    ("type", 166, 226, 46),
    ("literal", 190, 132, 255),
    ("number", 190, 132, 255),
    ("string", 230, 219, 116),
    ("title", 166, 226, 46),
    ("title.function", 166, 226, 46),
    ("title.class", 166, 226, 46),
    ("title.class.inherited", 166, 226, 46),
    ("params", 253, 151, 31),
    ("comment", 117, 113, 94),
    ("meta", 117, 113, 94),
    ("attr", 166, 226, 46),
    ("attribute", 166, 226, 46),
    ("variable", 255, 255, 255),
    ("variable.language", 255, 255, 255),
    ("property", 255, 255, 255),
    ("operator", 249, 38, 114),
    ("punctuation", 248, 248, 242),
    ("symbol", 190, 132, 255),
    ("regexp", 230, 219, 116),
    ("subst", 248, 248, 242),
)
_CATPPUCCIN_MACCHIATO: dict[str, tuple[int, int, int]] = {
    "keyword": (198, 160, 246),
    "_storage": (198, 160, 246),
    "built_in": (237, 135, 150),
    "type": (238, 212, 159),
    "literal": (245, 169, 127),
    "number": (245, 169, 127),
    "string": (166, 218, 149),
    "title": (138, 173, 244),
    "title.function": (138, 173, 244),
    "title.class": (238, 212, 159),
    "title.class.inherited": (238, 212, 159),
    "params": (238, 153, 160),
    "comment": (110, 115, 141),
    "meta": (198, 160, 246),
    "attr": (238, 212, 159),
    "attribute": (238, 212, 159),
    "variable": (202, 211, 245),
    "variable.language": (237, 135, 150),
    "property": (202, 211, 245),
    "operator": (145, 215, 227),
    "punctuation": (147, 154, 183),
    "symbol": (237, 135, 150),
    "regexp": (245, 189, 230),
    "subst": (202, 211, 245),
}


def _syntax_scope_pattern() -> re.Pattern[str]:
    parts: list[str] = []
    for i, (scope, r, g, b) in enumerate(_SYNTAX_DARK_MAP):
        var = rf"({_ID})" if i == 0 else r"\1"
        parts.append(rf'\["{re.escape(scope)}",{var}\({r},{g},{b}\)\]')
    return re.compile(r"new Map\(\[" + ",".join(parts) + r"\]\)")


def _syntax_scope_replacement(m: re.Match[str]) -> str:
    var = m.group(1)
    entries = ",".join(
        f'["{scope}",{var}'
        f"({_CATPPUCCIN_MACCHIATO[scope][0]},"
        f"{_CATPPUCCIN_MACCHIATO[scope][1]},"
        f"{_CATPPUCCIN_MACCHIATO[scope][2]})]"
        for scope, *_ in _SYNTAX_DARK_MAP
    )
    return f"new Map([{entries}])"


CATPPUCCIN_SYNTAX = PatchSet(
    name="catppuccin-syntax-scopes",
    patches=(
        Patch(
            name="catppuccin-syntax-scopes",
            pattern=_syntax_scope_pattern(),
            replacement=_syntax_scope_replacement,
        ),
    ),
    verify_present=(re.compile(r'new Map\(\[\["keyword",[\w$]+\(198,160,246\)'),),
    min_version=_V_2_1_151,
)


# --- provider brand patch sets ----------------------------------------------
#
# Applied additively on top of the defaults when `ccpatch apply --brand <name>`
# is given. A brand bakes provider identity into the binary (startup label, and
# later theme/thinking-verbs/thinker-symbol). Brand patterns are written as raw
# strings with inline [\w$]+ (no f-string) to keep the regex braces readable.

# Collapse the startup-title ternary so it always renders the brand label
# instead of "Claude Code" (ports cc-mirror's native-ui-hardening
# startup-title-brand). The label is baked per brand.
_LABEL_PATTERN = re.compile(
    r'([\w$]+)=([\w$]+)\?([\w$]+)\.createElement\(\2\.Title,null\):'
    r'\3\.createElement\(([\w$]+),\{bold:!0\},"Claude Code"\)'
)


def _startup_label_patch(label: str) -> Patch:
    return Patch(
        name="startup-title-brand",
        pattern=_LABEL_PATTERN,
        replacement=rf'\g<1>=\g<3>.createElement(\g<4>,{{bold:!0}},"{label}")',
    )


# Thinking-spinner verbs and glyphs (ports tweakcc thinkingVerbs /
# thinkerSymbolChars). thinker-symbol-speed is skipped (fixed upstream in
# >=2.1.27), and thinker-symbol-{width,mirror} have ambiguous anchors on 2.1.170
# (7 and 2 matches, one of which is the verbs mirror), so they're omitted.
# Verbs may carry \xNN escapes (e.g. Flamb\xE9ing), hence the char class.
_VERB_CHAR = r"[A-Z][a-z'é\-\\xA-F0-9]+"
_PRESENT_VERBS = re.compile(rf'''\[("{_VERB_CHAR}in[g']",?){{50,}}\]''')
_PAST_VERBS = re.compile(rf'''\[("{_VERB_CHAR}ed",?){{5,}}\]''')
_SYM = (
    r"(?:[·✢*✳✶✻✽]|\\u00b7|\\xb7|\\u2722|\\x2a|\\u002a"
    r"|\\u2733|\\u2736|\\u273b|\\u273d)"
)
_THINKER_SYMBOLS = re.compile(rf'''\["{_SYM}",\s*(?:"{_SYM}",?\s*)+\]''', re.IGNORECASE)


def _json_array(items: list[str]) -> str:
    return json.dumps(items, ensure_ascii=False, separators=(",", ":"))


def _verb_symbol_patches(verbs: list[str], symbols: list[str]) -> tuple[Patch, ...]:
    present = _json_array(verbs)
    past = _json_array([re.sub(r"ing$", "ed", v) for v in verbs])
    glyphs = _json_array(symbols)
    return (
        Patch("thinking-verbs-present", _PRESENT_VERBS, lambda _m: present),
        Patch("thinking-verbs-past", _PAST_VERBS, lambda _m: past),
        Patch("thinker-symbol-chars", _THINKER_SYMBOLS, lambda _m: glyphs),
    )


_KIMI_VERBS = [
    "Sparking",
    "Glinting",
    "Flowing",
    "Weaving",
    "Indexing",
    "Synthesizing",
    "Refining",
    "Composing",
    "Routing",
    "Resolving",
    "Calibrating",
    "Compiling",
]
_KIMI_SYMBOLS = ["·", "•", "◦", "•"]


# Commit/PR co-author. Claude Code computes it as
# `recognizedClaudeModel ? "Claude "+name : "Claude Fable 5"`; our providers'
# models aren't recognized, so it falls back to "Claude Fable 5". We replace the
# whole ternary with a lookup of the *runtime* model id (H = group 3) in a
# per-brand display-name map, falling back to the raw id (`??H`). So a glm-5-turbo
# session attributes to "GLM 5 Turbo", not a fixed flagship name. The map is keyed
# lowercase and H is lowercased at lookup, since Claude Code lowercases the id.
# Brand-only -- the plain `claude` build keeps the Claude display name. The other
# "Claude Fable 5" (the Fable model constant) lacks the ternary, so it is untouched.
_ATTRIBUTION_RE = re.compile(
    r'([\w$]+)=([\w$]+)\(([\w$]+)\)!==null\?([\w$]+)\(\3\):"Claude Fable 5"'
)


def _attribution_patch(model_map: dict[str, str]) -> Patch:
    table = json.dumps(model_map, ensure_ascii=False, separators=(",", ":"))

    def repl(m: re.Match[str]) -> str:
        var, model = m.group(1), m.group(3)
        return f"{var}=({table})[(''+{model}).toLowerCase()]??{model}"

    return Patch(name="attribution-model", pattern=_ATTRIBUTION_RE, replacement=repl)


_ZAI_VERBS = [
    "Calibrating",
    "Indexing",
    "Synthesizing",
    "Optimizing",
    "Routing",
    "Vectorizing",
    "Mapping",
    "Compiling",
    "Refining",
    "Auditing",
    "Aligning",
    "Balancing",
    "Forecasting",
    "Resolving",
    "Validating",
    "Benchmarking",
    "Assembling",
    "Delivering",
]
_ZAI_SYMBOLS = [".", "o", "O", "0", "O", "o"]

_MINIMAX_VERBS = [
    "Warping",
    "Nebulizing",
    "Phasing",
    "Refracting",
    "Tunneling",
    "Ionizing",
    "Polarizing",
    "Cascading",
    "Crystallizing",
    "Entangling",
    "Diffracting",
    "Converging",
    "Pulsing",
    "Transcoding",
]
_MINIMAX_SYMBOLS = ["⟡", "◈", "⬡", "◇", "⬡", "◈"]


_IDENTITY_PREFIX = "You are Claude Code, Anthropic's official CLI for Claude"


def _identity_patch(model_name: str) -> Patch:
    # "You are Claude Code, ..." -> "You are <model> running in Claude Code, ..."
    return Patch(
        name="identity-model",
        pattern=re.compile(re.escape(_IDENTITY_PREFIX)),
        replacement=lambda _m: f"You are {model_name} running in {_IDENTITY_PREFIX[8:]}",
    )


def _email_patch(domain: str) -> Patch:
    return Patch(
        name="attribution-email",
        pattern=re.compile(re.escape("noreply@anthropic.com")),
        replacement=lambda _m: f"noreply@{domain}",
    )


def _provider_brand(
    *,
    name: str,
    label: str,
    verbs: list[str],
    symbols: list[str],
    identity_name: str,
    model_map: dict[str, str],
    email_domain: str,
) -> PatchSet:
    # Prefix the identity preamble with the flagship model, map the commit
    # co-author from the runtime model id to a clean display name, and point the
    # attribution email at the provider's domain. (The "Generated with Claude
    # Code" footer is the product name and is intentionally left alone.)
    return PatchSet(
        name=f"{name}-brand",
        patches=(
            _startup_label_patch(label),
            *_verb_symbol_patches(verbs, symbols),
            _attribution_patch(model_map),
            _identity_patch(identity_name),
            _email_patch(email_domain),
        ),
        verify_present=(
            re.compile(rf'createElement\([\w$]+,\{{bold:!0\}},"{re.escape(label)}"\)'),
            re.compile(re.escape(f'"{verbs[0]}","{verbs[1]}"')),
            re.compile(re.escape(_json_array(symbols))),
            re.compile(re.escape(f"You are {identity_name} running in Claude Code")),
            re.compile(re.escape(f"noreply@{email_domain}")),
        ),
        verify_absent=(re.compile(r'!==null\?[\w$]+\([\w$]+\):"Claude Fable 5"'),),
    )


def kimi_brand() -> PatchSet:
    return _provider_brand(
        name="kimi",
        label="Kimi Code",
        verbs=_KIMI_VERBS,
        symbols=_KIMI_SYMBOLS,
        identity_name="Kimi K2.7 Code",
        model_map={"kimi-k2.7-code": "Kimi K2.7 Code"},
        email_domain="kimi.com",
    )


def zai_brand() -> PatchSet:
    return _provider_brand(
        name="zai",
        label="Zai Cloud",
        verbs=_ZAI_VERBS,
        symbols=_ZAI_SYMBOLS,
        identity_name="GLM 5.2",
        model_map={
            "glm-5.2": "GLM 5.2",
            "glm-5-turbo": "GLM 5 Turbo",
            "glm-4.5-air": "GLM 4.5 Air",
            "glm-4.7": "GLM 4.7",
        },
        email_domain="z.ai",
    )


def minimax_brand() -> PatchSet:
    return _provider_brand(
        name="minimax",
        label="MiniMax Cloud",
        verbs=_MINIMAX_VERBS,
        symbols=_MINIMAX_SYMBOLS,
        identity_name="MiniMax M2.7",
        model_map={"minimax-m2.7": "MiniMax M2.7"},
        email_domain="minimax.io",
    )


_BRANDS: dict[str, Callable[[], PatchSet]] = {
    "kimi": kimi_brand,
    "minimax": minimax_brand,
    "zai": zai_brand,
}


def brand_patch_sets(brand: str | None) -> list[PatchSet]:
    """Patch sets for ``--brand <name>`` (empty when no brand requested).

    Version gating, if a brand patch needs it, is handled per-PatchSet via
    ``min_version``/``applies_to`` like the defaults.
    """
    if brand is None:
        return []
    builder = _BRANDS.get(brand)
    if builder is None:
        raise PatchError(f"unknown brand {brand!r}; known: {sorted(_BRANDS)}")
    return [builder()]


def default_patch_sets(version: Version | None) -> list[PatchSet]:
    """The patch sets applied by ``ccpatch apply`` (order matters)."""
    return [
        thinking_expanded(version),
        FABLE_MODEL,
        CHANNELS_ENABLED,
        DEV_CHANNEL_INHERITANCE,
        CATPPUCCIN_SYNTAX,
    ]
