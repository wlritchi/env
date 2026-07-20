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
from typing import TypedDict

Version = tuple[int, ...]


class ModelCosts(TypedDict):
    inputTokens: float
    outputTokens: float
    promptCacheWriteTokens: float
    promptCacheReadTokens: float
    webSearchRequests: float


ModelCostsByModel = dict[str, ModelCosts]


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
# >>> ACTIVATION CONTRACT -- read before changing dev-channel activation <<<
#
# ccpatch activates development channels by SCANNING THE DISPATCHING PROCESS'S
# OWN ARGV for --dangerously-load-development-channels (see _DISPATCH_DEV_CHANNELS
# below: it reads process.argv.slice(2) and reads NO environment variable). That
# "no env" property is load-bearing and enforced -- this PatchSet's verify_absent
# pins CLAUDE_DEV_CHANNELS to *absent*, so any change that re-introduces ambient
# env reading fails the patch by construction.
#
# Consequence for callers: a non-interactive launcher (bg-agent worker, daemon
# respawn, headless -p dispatch) MUST put --dangerously-load-development-channels
# on the LAUNCH ARGV. Exporting an env var alone does NOT activate channels --
# nothing here reads one, by design. A consumer that only has the value in an env
# var must translate env -> argv flag at the launch site.
#
# History/trap: an earlier shim read the channel list from the environment; that
# ambient-env path was dropped in favor of the native argv threading below. If
# activation ever breaks, fix it in the CONSUMER (make it pass the flag on argv)
# -- do NOT re-add env reading here; that reintroduces the exact regression class
# this verify_absent guard exists to stop.
#
# --dangerously-load-development-channels never reaches the bg-agent workers the
# agents view spawns. Three things drop it, all fixed natively here -- no env
# var, no wrapper:
#
#   1. Live dispatch: the worker argv (dispatchExtraArgs) is built by
#      $UH(HUH(cfg)), and $UH only serializes --settings/--plugin-dir/--add-dir/
#      --mcp-config/--strict-mcp-config -- it never carries channels. Append the
#      dev-channels (scanned from the dispatching process's own argv, which is
#      how the user passed them) so they land in dispatchExtraArgs -> the worker.
#   2. Persist/resume: those dispatch args double as the job's persisted
#      respawnFlags, filtered through the RfH (value-flag) + HE6 (multi-value)
#      allowlists on resume -- add the flag there (next to --channels) to survive.
#   3. Worker parse: dev channels need an interactive confirmation dialog (gated
#      on !isNonInteractiveSession) a bg worker can't show, so the worker skips
#      the parse. After that block, register the worker's OWN parsed specs for bg
#      sessions -- tagging each {dev:!0} (the allowlist bypass) and calling the
#      registrar directly (the parent already confirmed). Scoped to bg so other
#      non-interactive sessions (-p, etc.) still require the dialog.
#
# All length-free: extend two Set literals, append one array element, and append
# one statement after the parse block (captured verbatim). Minified identifiers
# are captured; only stable literals are pinned.

# Forward the flag through the bg-worker respawn allowlists, next to --channels.
_DEV_CHANNEL_FORWARD = (
    Patch(
        name="dev-channel-respawn-value-flag",
        pattern=re.compile(re.escape('"--channels","--permission-prompt-tool"')),
        replacement=(
            '"--channels","--dangerously-load-development-channels",'
            '"--permission-prompt-tool"'
        ),
    ),
    Patch(
        name="dev-channel-respawn-multivalue",
        pattern=re.compile(re.escape('"--file","--channels"]')),
        replacement='"--file","--channels","--dangerously-load-development-channels"]',
    ),
)

# Append dev-channels to the dispatch serializer ($UH) return array, scanned from
# the dispatching process's own argv (variadic: collect bare tokens after the
# flag, stop at the next -flag; also accept the --flag=value form).
_DISPATCH_DEV_CHANNELS = (
    "(()=>{let _dc=[],_co=!1;for(let _ar of process.argv.slice(2)){"
    'if(_ar==="--dangerously-load-development-channels")_co=!0;'
    'else if(_ar.startsWith("--dangerously-load-development-channels="))'
    '_dc.push(_ar.slice(_ar.indexOf("=")+1));'
    'else if(_ar.startsWith("-"))_co=!1;'
    "else if(_co)_dc.push(_ar)}"
    'return _dc.length?["--dangerously-load-development-channels",..._dc]:[]})()'
)


def _dispatch_forward_replacement(m: re.Match[str]) -> str:
    # Insert before the closing `]` of the $UH return array, after the
    # --strict-mcp-config element (captured as group 1).
    return f"{m.group(1)},...{_DISPATCH_DEV_CHANNELS}]"


def _dev_channel_replacement(m: re.Match[str]) -> str:
    base, parse, register, dev_arg = (
        m.group(2),
        m.group(3),
        m.group(4),
        m.group(5),
    )
    # Brace-wrap so the appended statement ends in `}` -- the source continues
    # immediately with `if(...)`, and `n9H(...)if(` (no separator) is a syntax
    # error that the marker-only structural verify would not catch.
    return m.group(0) + (
        'if(process.env.CLAUDE_CODE_SESSION_KIND==="bg"&&'
        f"{dev_arg}&&{dev_arg}.length>0){{"
        f"{register}([...{base},...{parse}({dev_arg},"
        '"--dangerously-load-development-channels")'
        ".map((devEntry)=>({...devEntry,dev:!0}))])}"
    )


DEV_CHANNEL_INHERITANCE = PatchSet(
    name="dev-channel-inheritance",
    patches=(
        *_DEV_CHANNEL_FORWARD,
        Patch(
            name="dev-channel-dispatch-forward",
            pattern=re.compile(
                r'(\.\.\.[\w$]+\.strictMcpConfig\?\["--strict-mcp-config"\]:\[\])\]'
            ),
            replacement=_dispatch_forward_replacement,
        ),
        Patch(
            name="dev-channel-bg-register",
            pattern=re.compile(
                rf"if\(({_ID})&&\1\.length>0\)({_ID})=({_ID})\(\1,\"--channels\"\),"
                rf"({_ID})\(\2\);if\(!{_ID}\)\{{if\(({_ID})&&\5\.length>0\)"
                rf"{_ID}=\3\(\5,\"--dangerously-load-development-channels\"\)\}}"
            ),
            replacement=_dev_channel_replacement,
        ),
    ),
    verify_present=(
        re.compile(
            r'"--channels","--dangerously-load-development-channels",'
            r'"--permission-prompt-tool"'
        ),
        re.compile(
            r'"--file","--channels","--dangerously-load-development-channels"\]'
        ),
        re.compile(r'strictMcpConfig\?\["--strict-mcp-config"\]:\[\],\.\.\.\(\(\)=>'),
        re.compile(r'CLAUDE_CODE_SESSION_KIND==="bg"&&[\w$]+&&[\w$]+\.length>0\)'),
    ),
    verify_absent=(re.compile(r"CLAUDE_DEV_CHANNELS"),),
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

# --- thinking summaries in non-interactive sessions (2.1.151+) ---------------
#
# `showThinkingSummaries` (settings) drives the request's thinking.display, but
# the binary only applies it as the default when the session is interactive:
# `else if(!isInteractive()&&showThinkingSummaries())n8.display="summarized"`.
# Drop the interactive gate so the setting governs `display` in -p / other
# non-interactive sessions too -- otherwise `claude -p` requests omitted
# thinking and the on-disk transcript has no summaries. An explicit
# --thinking-display still wins (it is checked in the preceding branch).
THINKING_SUMMARIES_NONINTERACTIVE = PatchSet(
    name="thinking-summaries-noninteractive",
    patches=(
        Patch(
            name="ungate-thinking-display-default",
            pattern=re.compile(
                rf'else if\(!{_ID}\(\)&&({_ID}\(\))\)({_ID})\.display="summarized"'
            ),
            replacement=r'else if(\1)\2.display="summarized"',
        ),
    ),
    verify_present=(re.compile(r'else if\([\w$]+\(\)\)[\w$]+\.display="summarized"'),),
    verify_absent=(
        re.compile(r'else if\(![\w$]+\(\)&&[\w$]+\(\)\)[\w$]+\.display="summarized"'),
    ),
    min_version=_V_2_1_151,
)


# Model-facing description of the injected compact_session tool. The build serializes
# every tool for each API request by reading its `prompt()` method (see gtf/hI8 in the
# bundle); a tool that omits `prompt()` throws `H.prompt is not a function` on *every*
# message, so -- like every real tool -- we supply prompt() (model-facing text),
# description(), and a searchHint. The tool constructor copies own-properties only
# (it does not synthesize prompt() from description()), so all three must be explicit.
_COMPACT_DESC = (
    "Schedule compaction of this session: summarize the conversation so far to free "
    "up context. Runs at the end of the current turn if compaction is enabled and "
    "healthy; any in-flight work in this turn completes first. Use proactively when "
    "context is filling up instead of waiting for the automatic threshold."
)
# Short hint shown in CLAUDE_CODE_SIMPLE mode (gtf() returns searchHint when set).
_COMPACT_HINT = "schedule end-of-turn compaction to free up context"


def _compact_tool_object(schema_ns: str) -> str:
    # The tool object literal handed to the build's tool constructor. `schema_ns` is
    # the captured zod-like namespace so the empty input schema (`<ns>.object({})`)
    # resolves on every platform.
    return (
        '{name:"compact_session",'
        f'searchHint:"{_COMPACT_HINT}",'
        f'async description(){{return"{_COMPACT_DESC}"}},'
        f'async prompt(){{return"{_COMPACT_DESC}"}},'
        f"get inputSchema(){{return {schema_ns}.object({{}})}},"
        "isReadOnly(){return!0},isConcurrencySafe(){return!0},"
        "async call(H,$){let W=Date.now(),Z=globalThis.__ccLastSelfCompact||0;"
        "if(W-Z<3e5){let Q=Math.round((W-Z)/1e3);"
        "return{data:{message:`compact_session was called ${Q}s ago; "
        "not rescheduling within the 300s cooldown.`}}}"
        "return globalThis.__ccPendingCompact=!0,globalThis.__ccLastSelfCompact=W,"
        '{data:{message:"Compaction scheduled: runs at the end of this turn if compaction '
        "is enabled and healthy. Context will be summarized; in-flight work in this turn "
        'completes first."}}},'
        # the framework passes the result's `.data` payload here (map(t.data,id)), so
        # read H.message directly -- not H.data.message (that double-dip was the bug)
        "mapToolResultToToolResultBlockParam(H,$){"
        'return{tool_use_id:$,type:"tool_result",content:H.message}}}'
    )


def _define_compact_tool(m: re.Match[str]) -> str:
    # m.1 = the TodoWrite schema declarator (re-emitted verbatim); m.2 = the zod-like
    # schema namespace; m.3 = the tool constructor. Splice both into the injected tool
    # so it uses the same symbols the real tools in this build do.
    schema_ns, builder = m.group(2), m.group(3)
    tool = f"globalThis.__ccCompactTool={builder}({_compact_tool_object(schema_ns)})"
    return f"{m.group(1)}{tool},"


def _register_compact(m: re.Match[str]) -> str:
    # m.1 = the tool-registry function name; the array body (from its first tool
    # identifier on) is preserved by the pattern's lookahead. Prepend the guarded
    # spread so the compact tool is registered iff it was defined (init-order-proof).
    return (
        f"function {m.group(1)}()"
        "{return[...(globalThis.__ccCompactTool?[globalThis.__ccCompactTool]:[]),"
    )


def _force_compact(m: re.Match[str]) -> str:
    # xXf is the *proactive* autocompact decision. It skips itself via this guard
    # (m.group(0)) whenever the threshold source is "auto" -- which is every model
    # except the two in SXf ({sonnet-4-6, opus-4-6}) at <1M context. Opus 4.8 (1M
    # window) and Haiku 4.5 (absent from SXf) both resolve to "auto", so the guard
    # returns before the verdict line where the flag was previously read, leaving
    # compact_session a silent no-op. Consume the pending-compact flag BEFORE the
    # guard so an explicit compact_session forces compaction on every model, then
    # fall through to the untouched guard for the normal token-threshold path.
    return (
        "if(globalThis.__ccPendingCompact)"
        "return globalThis.__ccPendingCompact=!1,!0;" + m.group(0)
    )


COMPACT_SESSION = PatchSet(
    name="compact-session-tool",
    patches=(
        Patch(
            "define-compact-session-tool",
            re.compile(
                r'(([\w$]+)\.object\(\{oldTodos:[\w$]+\(\)\.describe\('
                r'"The todo list before the update"\),newTodos:[\w$]+\(\)\.describe\('
                r'"The todo list after the update"\)\}\)\),)(?=[\w$]+=([\w$]+)\(\{name:)'
            ),
            _define_compact_tool,
        ),
        Patch(
            "register-compact-session-in-toollist",
            re.compile(r"function ([\w$]+)\(\)\{return\[(?=[\w$]+,)"),
            _register_compact,
        ),
        Patch(
            "force-compaction-on-flag",
            # The source=="auto" skip guard in xXf: if(Ue()&&!ni()&&!X4$($,q))return!1
            # -- three predicate calls, the last taking (model, window). Consume the
            # pending-compact flag just before it (lookbehind blocks a second apply).
            re.compile(
                r"(?<!=!1,!0;)if\([\w$]+\(\)&&![\w$]+\(\)&&!"
                r"[\w$]+\([\w$]+,[\w$]+\)\)return!1"
            ),
            _force_compact,
        ),
    ),
    verify_present=(
        re.compile(r'globalThis\.__ccCompactTool=[\w$]+\(\{name:"compact_session"'),
        # the tool must expose prompt() -- API tool serialization (gtf) reads it, and
        # a tool without it throws `H.prompt is not a function` on every request
        re.compile(r'name:"compact_session".{0,600}async prompt\(\)\{return'),
        re.compile(
            r'\.\.\.\(globalThis\.__ccCompactTool\?\[globalThis\.__ccCompactTool\]:\[\]\),[\w$]+,'
        ),
        # the flag is consumed *before* the source=="auto" skip guard in xXf, so an
        # explicit compact_session reaches the compactor even on models that skip
        # proactive autocompact (opus-4-8 1M window, haiku absent from SXf)
        re.compile(
            r"if\(globalThis\.__ccPendingCompact\)return "
            r"globalThis\.__ccPendingCompact=!1,!0;if\([\w$]+\(\)&&!"
        ),
    ),
    verify_absent=(
        re.compile(r'The todo list after the update"\)\}\)\),[\w$]+=[\w$]+\(\{name:'),
        re.compile(r'function [\w$]+\(\)\{return\[[\w$]+,'),
        # not double-applied: the injected early-return is never immediately followed
        # by a second copy of itself
        re.compile(r"=!1,!0;if\(globalThis\.__ccPendingCompact\)return globalThis"),
    ),
    min_version=(2, 1, 170),
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

_OPENAI_VERBS = [
    "Reasoning",
    "Planning",
    "Inspecting",
    "Synthesizing",
    "Refining",
    "Tracing",
    "Reviewing",
    "Composing",
    "Testing",
    "Patching",
    "Checking",
    "Resolving",
    "Caching",
    "Streaming",
]
_OPENAI_SYMBOLS = [".", "o", "O", "0", "O", "o"]


_IDENTITY_PREFIX = "You are Claude Code, Anthropic's official CLI for Claude"


def _identity_patch(model_name: str) -> Patch:
    # "You are Claude Code, ..." -> "You are <model> running in Claude Code, ..."
    return Patch(
        name="identity-model",
        pattern=re.compile(re.escape(_IDENTITY_PREFIX)),
        replacement=lambda _m: (
            f"You are {model_name} running in {_IDENTITY_PREFIX[8:]}"
        ),
    )


def _email_patch(domain: str) -> Patch:
    return Patch(
        name="attribution-email",
        pattern=re.compile(re.escape("noreply@anthropic.com")),
        replacement=lambda _m: f"noreply@{domain}",
    )


_MODEL_COSTS_RE = re.compile(
    r"(\},[\w$]+=[\w$]+;[\w$]+=\{)(\[[\w$]+\([\w$]+\.firstParty\)\]:)"
)


def _model_costs_patch(model_costs: ModelCostsByModel) -> Patch:
    # Claude Code computes statusline/session cost from its own per-model table.
    # Provider-branded builds can add provider model IDs to that table so unknown
    # models don't fall back to the default Claude rate.
    table = ",".join(
        f"{json.dumps(model, separators=(',', ':'))}:"
        f"{{inputTokens:{costs['inputTokens']},"
        f"outputTokens:{costs['outputTokens']},"
        f"promptCacheWriteTokens:{costs['promptCacheWriteTokens']},"
        f"promptCacheReadTokens:{costs['promptCacheReadTokens']},"
        f"webSearchRequests:{costs['webSearchRequests']}}}"
        for model, costs in model_costs.items()
    )

    def repl(m: re.Match[str]) -> str:
        return f"{m.group(1)}{table},{m.group(2)}"

    return Patch(name="model-costs", pattern=_MODEL_COSTS_RE, replacement=repl)


# Skip the first-run onboarding flow (theme picker / login walkthrough). The
# wrapper used to seed .claude.json with hasCompletedOnboarding:true; baking the
# skip into the binary lets the variant ship no seeded config. Only the first-run
# trigger is neutralized -- the CLAUDE_CODE_TEAM_ONBOARDING env override
# (banner/step) still fires. Brand-only; the plain `claude` build keeps onboarding.
_SKIP_ONBOARDING = Patch(
    name="skip-onboarding",
    pattern=re.compile(
        r"!([\w$]+)\.hasCompletedOnboarding\|\|"
        r"\(process\.env\.CLAUDE_CODE_TEAM_ONBOARDING"
    ),
    replacement="!1||(process.env.CLAUDE_CODE_TEAM_ONBOARDING",
)


# Print the provider splash on interactive startup, replacing the wrapper's
# `cat splash` step. Injected just past the print-mode early returns (so it only
# runs in the TUI), guarded on isTTY so piped output stays clean. The art (ANSI
# included) is embedded as a JSON string literal -- valid JS, control chars
# escaped as \uXXXX -- so there is no template-literal/backtick wrinkle.
_SPLASH_RE = re.compile(
    r"(mcpApprovalSkipWarning:[\w$]+\};)(let [\w$]+=[\w$]+\(\),[\w$]+=!1;)"
)


def _splash_patch(splash: str) -> Patch:
    text = splash if splash.endswith("\n") else splash + "\n"
    literal = json.dumps(text, ensure_ascii=False)
    inject = f"if(process.stdout.isTTY)process.stdout.write({literal});"

    def repl(m: re.Match[str]) -> str:
        return m.group(1) + inject + m.group(2)

    return Patch(name="startup-splash", pattern=_SPLASH_RE, replacement=repl)


def _provider_brand(
    *,
    name: str,
    label: str,
    verbs: list[str],
    symbols: list[str],
    identity_name: str,
    model_map: dict[str, str],
    email_domain: str,
    model_costs: ModelCostsByModel | None = None,
    splash: str | None = None,
) -> PatchSet:
    # Prefix the identity preamble with the flagship model, map the commit
    # co-author from the runtime model id to a clean display name, point the
    # attribution email at the provider's domain, skip first-run onboarding, and
    # (when art is supplied) print the splash on interactive startup. (The
    # "Generated with Claude Code" footer is the product name and is left alone.)
    patches: tuple[Patch, ...] = (
        _startup_label_patch(label),
        *_verb_symbol_patches(verbs, symbols),
        _attribution_patch(model_map),
        _identity_patch(identity_name),
        _email_patch(email_domain),
    )
    if model_costs is not None:
        patches = (*patches, _model_costs_patch(model_costs))
    patches = (*patches, _SKIP_ONBOARDING)

    present: tuple[re.Pattern[str], ...] = (
        re.compile(rf'createElement\([\w$]+,\{{bold:!0\}},"{re.escape(label)}"\)'),
        re.compile(re.escape(f'"{verbs[0]}","{verbs[1]}"')),
        re.compile(re.escape(_json_array(symbols))),
        re.compile(re.escape(f"You are {identity_name} running in Claude Code")),
        re.compile(re.escape(f"noreply@{email_domain}")),
        re.compile(r"!1\|\|\(process\.env\.CLAUDE_CODE_TEAM_ONBOARDING"),
    )
    if model_costs is not None:
        present = (
            *present,
            *(
                re.compile(re.escape(f'"{model}":{{inputTokens:'))
                for model in model_costs
            ),
        )
    if splash is not None:
        patches = (*patches, _splash_patch(splash))
        present = (
            *present,
            re.compile(r"\};if\(process\.stdout\.isTTY\)process\.stdout\.write\("),
        )
    return PatchSet(
        name=f"{name}-brand",
        patches=patches,
        verify_present=present,
        verify_absent=(
            re.compile(r'!==null\?[\w$]+\([\w$]+\):"Claude Fable 5"'),
            re.compile(
                r"\.hasCompletedOnboarding\|\|\(process\.env\.CLAUDE_CODE_TEAM_ONBOARDING"
            ),
        ),
    )


_KIMI_MODEL_COSTS: ModelCostsByModel = {
    "kimi-k2.7-code": {
        "inputTokens": 0.95,
        "outputTokens": 4,
        "promptCacheWriteTokens": 0.95,
        "promptCacheReadTokens": 0.19,
        "webSearchRequests": 0,
    },
}


def kimi_brand(splash: str | None = None) -> PatchSet:
    return _provider_brand(
        name="kimi",
        label="Kimi Code",
        verbs=_KIMI_VERBS,
        symbols=_KIMI_SYMBOLS,
        identity_name="Kimi K2.7 Code",
        model_map={"kimi-k2.7-code": "Kimi K2.7 Code"},
        email_domain="kimi.com",
        model_costs=_KIMI_MODEL_COSTS,
        splash=splash,
    )


_ZAI_MODEL_COSTS: ModelCostsByModel = {
    "glm-5.2": {
        "inputTokens": 1.4,
        "outputTokens": 4.4,
        "promptCacheWriteTokens": 1.4,
        "promptCacheReadTokens": 0.26,
        "webSearchRequests": 0,
    },
    "glm-5-turbo": {
        "inputTokens": 1.2,
        "outputTokens": 4.0,
        "promptCacheWriteTokens": 1.2,
        "promptCacheReadTokens": 0.2,
        "webSearchRequests": 0,
    },
    "glm-4.5-air": {
        "inputTokens": 0.2,
        "outputTokens": 1.1,
        "promptCacheWriteTokens": 0.2,
        "promptCacheReadTokens": 0.03,
        "webSearchRequests": 0,
    },
}


def zai_brand(splash: str | None = None) -> PatchSet:
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
        model_costs=_ZAI_MODEL_COSTS,
        splash=splash,
    )


_MINIMAX_MODEL_COSTS: ModelCostsByModel = {
    "minimax-m2.7": {
        "inputTokens": 0.3,
        "outputTokens": 1.2,
        "promptCacheWriteTokens": 0.375,
        "promptCacheReadTokens": 0.06,
        "webSearchRequests": 0,
    },
    "MiniMax-M2.7": {
        "inputTokens": 0.3,
        "outputTokens": 1.2,
        "promptCacheWriteTokens": 0.375,
        "promptCacheReadTokens": 0.06,
        "webSearchRequests": 0,
    },
}


def minimax_brand(splash: str | None = None) -> PatchSet:
    return _provider_brand(
        name="minimax",
        label="MiniMax Cloud",
        verbs=_MINIMAX_VERBS,
        symbols=_MINIMAX_SYMBOLS,
        identity_name="MiniMax M2.7",
        model_map={"minimax-m2.7": "MiniMax M2.7"},
        email_domain="minimax.io",
        model_costs=_MINIMAX_MODEL_COSTS,
        splash=splash,
    )


_OPENAI_MODEL_COSTS: ModelCostsByModel = {
    "gpt-5.4": {
        "inputTokens": 2.5,
        "outputTokens": 15,
        "promptCacheWriteTokens": 0,
        "promptCacheReadTokens": 0.25,
        "webSearchRequests": 0.01,
    },
    "gpt-5.5": {
        "inputTokens": 5,
        "outputTokens": 30,
        "promptCacheWriteTokens": 0,
        "promptCacheReadTokens": 0.5,
        "webSearchRequests": 0.01,
    },
    "gpt-5.4-mini": {
        "inputTokens": 0.75,
        "outputTokens": 4.5,
        "promptCacheWriteTokens": 0,
        "promptCacheReadTokens": 0.075,
        "webSearchRequests": 0.01,
    },
}


def openai_brand(splash: str | None = None) -> PatchSet:
    return _provider_brand(
        name="openai",
        label="OpenAI Codex",
        verbs=_OPENAI_VERBS,
        symbols=_OPENAI_SYMBOLS,
        identity_name="GPT-5.5",
        model_map={
            "gpt-5.3-codex-spark": "GPT-5.3 Codex Spark",
            "gpt-5.4": "GPT-5.4",
            "gpt-5.4-mini": "GPT-5.4 Mini",
            "gpt-5.5": "GPT-5.5",
            "gpt-5.5-pro": "GPT-5.5 Pro",
        },
        email_domain="openai.com",
        model_costs=_OPENAI_MODEL_COSTS,
        splash=splash,
    )


_BRANDS: dict[str, Callable[[str | None], PatchSet]] = {
    "kimi": kimi_brand,
    "minimax": minimax_brand,
    "openai": openai_brand,
    "zai": zai_brand,
}


def brand_patch_sets(brand: str | None, splash: str | None = None) -> list[PatchSet]:
    """Patch sets for ``--brand <name>`` (empty when no brand requested).

    ``splash`` is the optional startup-splash art embedded into the interactive
    entry. Version gating, if a brand patch needs it, is handled per-PatchSet via
    ``min_version``/``applies_to`` like the defaults.
    """
    if brand is None:
        return []
    builder = _BRANDS.get(brand)
    if builder is None:
        raise PatchError(f"unknown brand {brand!r}; known: {sorted(_BRANDS)}")
    return [builder(splash)]


def default_patch_sets(version: Version | None) -> list[PatchSet]:
    """The patch sets applied by ``ccpatch apply`` (order matters)."""
    return [
        thinking_expanded(version),
        FABLE_MODEL,
        CHANNELS_ENABLED,
        DEV_CHANNEL_INHERITANCE,
        CATPPUCCIN_SYNTAX,
        THINKING_SUMMARIES_NONINTERACTIVE,
        COMPACT_SESSION,
    ]
