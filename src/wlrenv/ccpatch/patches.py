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
from typing import NotRequired, TypedDict

Version = tuple[int, ...]


class ModelCosts(TypedDict):
    inputTokens: float
    outputTokens: float
    promptCacheWriteTokens: float
    promptCacheReadTokens: float
    webSearchRequests: float


ModelCostsByModel = dict[str, ModelCosts]


class MultiProviderModel(TypedDict):
    wireModel: str
    label: str
    description: str
    contextWindow: int
    maxOutputTokens: int
    costs: ModelCosts


class MultiProviderDefinition(TypedDict):
    provider: str
    attributionDomain: str
    baseURL: str
    tokenEnv: str
    availabilityEnv: NotRequired[str]
    baseURLEnv: NotRequired[str]
    defaultHeaders: dict[str, str]
    models: tuple[MultiProviderModel, ...]


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
    requires_version: bool = False

    def applies_to(self, version: Version | None) -> bool:
        if version is None:
            return not self.requires_version
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
_V_2_1_174 = (2, 1, 174)

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

# --- provider environment inheritance for background agents (2.1.174) --------
#
# A background daemon can outlive the client that dispatches a job. Send the
# current provider configuration for each authenticated socket dispatch. Do not
# store this configuration in job files. A null value removes a value that a
# cold or prewarmed worker inherited from an earlier process.
_PROVIDER_ENV_PROTOCOL_VERSION = 3
_PROVIDER_ENV_GROUPS = re.compile(
    rf'(?P<selection>{_ID})=\["CLAUDE_CODE_USE_BEDROCK",'
    rf'(?P<selection_tail>.{{0,1000}}?)\],(?P<base_urls>{_ID})='
    r'\["ANTHROPIC_BASE_URL",'
    rf'(?P<base_url_tail>.{{0,1000}}?)\],(?P<credentials>{_ID})='
    r'\["ANTHROPIC_API_KEY","ANTHROPIC_AUTH_TOKEN",'
    rf'(?P<credential_tail>.{{0,1000}}?)\],(?P<skip_auth>{_ID})='
    r'\["CLAUDE_CODE_SKIP_BEDROCK_AUTH",'
    rf'(?P<skip_auth_tail>.{{0,1000}}?)\],(?P<models>{_ID})='
    r'\["ANTHROPIC_MODEL",'
    rf'(?P<model_tail>.{{0,3000}}?)\],(?P<custom_models>{_ID})='
    r'\["ANTHROPIC_CUSTOM_MODEL_OPTION",'
    rf'(?P<custom_model_tail>.{{0,1000}}?)\],(?P<recognized>{_ID})=new Set'
)
_PROVIDER_ENV_SNAPSHOT = re.compile(
    rf'function (?P<snapshot>{_ID})\(\)\{{let (?P<result>{_ID})=\{{\}};'
    rf'for\(let (?P<key>{_ID}) of (?P<allowlist>{_ID})\)\{{let '
    rf'(?P<value>{_ID})=process\.env\[(?P=key)\];if\((?P=value)===void 0\)continue;'
    rf'if\((?P=value)===""&&(?P=key)!=="CLAUDE_SECURESTORAGE_CONFIG_DIR"\)continue;'
    rf'(?P=result)\[(?P=key)\]=(?P=value)\}}return (?P=result)\}}'
)
_PROVIDER_ENV_SCHEMA = re.compile(
    rf'(?P<schema>{_ID})\.object\(\{{proto:(?P<proto>{_ID}),op:'
    rf'(?P=schema)\.literal\("dispatch"\),d:(?P<dispatch>{_ID})\(\),'
    rf'timeoutMs:(?P=schema)\.number\(\),auth:'
)
_PROVIDER_ENV_PERSISTED_DEFAULT = re.compile(
    rf'(?P<isolation>{_ID})=(?P<source>{_ID})==="repl"\?"none":'
    rf'(?P<options>{_ID})\?\.bgIsolation,(?P<provider>{_ID})='
    rf'(?P=options)\?\.providerEnv\?\?(?P<snapshot>{_ID})\(\),'
)
_PROVIDER_ENV_SOCKET = re.compile(
    rf'(?P<call>{_ID})\(\{{proto:(?P<proto>{_ID}),op:"dispatch",d:'
    rf'\{{\.\.\.(?P<job>{_ID}),nonce:(?P<nonce>[^}}]+)\}},timeoutMs:5000,'
    rf'auth:await (?P<auth>{_ID})\(\)\}}'
)
_PROVIDER_ENV_SOCKET_RESULT = re.compile(
    rf'if\((?P<response>{_ID})\.ok&&(?P=response)\.op==="dispatch"\)'
    rf'(?P<success>return .{{0,500}}?);if\("code"in (?P=response)&&'
)
_PROVIDER_ENV_REDISPATCH_RESULT = re.compile(
    rf'if\((?P<response>{_ID})\.ok&&(?P=response)\.op==="dispatch"\)'
    rf'(?P<success>return .{{0,1000}}?\{{ok:!0,short:(?P<short>{_ID}),'
    rf'sessionId:(?P<session>{_ID}),idle:(?P<idle>{_ID}),name:(?P<name>{_ID}),'
    rf'rescued:!0\}})'
)
_PROVIDER_ENV_DAEMON_ACK = re.compile(
    rf'return (?P<respond>{_ID})\((?P<socket>{_ID}),\{{ok:!0,op:(?P<op>{_ID}),'
    rf'short:(?P<short>{_ID}),pid:(?P<worker>{_ID})\.record\.pid,'
)
_PROVIDER_ENV_CONTROL = re.compile(
    rf'case"dispatch":if\(!(?P<auth_check>{_ID})\((?P<request>{_ID})\.auth,'
    rf'(?P<control_key>{_ID})\)\)return (?P<respond>{_ID})\((?P<socket>{_ID}),'
    rf'(?P<auth_error>\{{ok:!1,.{{0,300}}?code:"EAUTH"\}})\);if\(await '
    rf'(?P<yield>{_ID})\(0\),(?P=socket)\.readableEnded\|\|(?P=socket)\.destroyed\)'
    rf'(?P<stale>.{{0,300}}?)return (?P<wait>{_ID})\((?P<handles>{_ID}),(?P=socket),'
    rf'"dispatch",(?P=request)\.d\.short,(?P=request)\.d\.nonce,'
    rf'(?P=request)\.timeoutMs,(?P<dispatch_cb>{_ID})\((?P=request)\.d\)'
)
_PROVIDER_ENV_WORKER = re.compile(
    rf'function (?P<env_builder>{_ID})\((?P<job>{_ID}),(?P<job_dir>{_ID}),'
    rf'(?P<snapshot_path>{_ID}),(?P<rv_sock>{_ID}),(?P<socket_auth>{_ID})\)'
    rf'\{{let (?P<ambient>{_ID})=\{{\.\.\.process\.env\}},(?P<env>{_ID})='
    rf'\{{\.\.\.(?P=ambient),(?P<body>.{{0,2000}}?)\}};if\(process\.env\.'
)
_PROVIDER_ENV_WORKER_FINAL = re.compile(
    rf'(?P<prefix>function (?P<env_builder>{_ID})\((?P<job>{_ID}),(?P<job_dir>{_ID}),'
    rf'(?P<snapshot_path>{_ID}),(?P<rv_sock>{_ID}),(?P<socket_auth>{_ID}),'
    rf'_ccProviderEnv\)\{{.{{0,500}}?let _ccProviderPayload=.{{0,5000}}?)'
    rf'return (?P<env>{_ID})\}}'
)
_PROVIDER_ENV_PATCHED_SNAPSHOT = re.compile(
    rf'function _ccProviderKeys\(\).{{0,7000}}?function (?P<snapshot>{_ID})\(\)'
    rf'\{{let {_ID}=\{{\}};for\(let {_ID} of _ccProviderKeys\(\)\)'
)
_PROVIDER_ENV_MANAGER = re.compile(
    rf'(?P<class_name>{_ID})\{{dispatch;spawnPty;getAuthSnapshot;via;record;'
)
_PROVIDER_ENV_CONSTRUCTOR = re.compile(
    rf'constructor\((?P<job>{_ID}),(?P<spawn>{_ID}),(?P<auth>{_ID}),'
    rf'(?P<via>{_ID}),(?P<record>{_ID})\)\{{this\.dispatch=(?P=job);'
)
_PROVIDER_ENV_STATIC_SPAWN = re.compile(
    rf'static spawn\((?P<job>{_ID}),(?P<spawn>{_ID}),(?P<auth>{_ID}),'
    rf'(?P<options>{_ID})\)\{{let (?P<worker>{_ID})=new (?P<class_name>{_ID})'
    rf'\((?P=job),(?P=spawn)\?\?(?P<default_spawn>{_ID})\(\),(?P=auth),"cold"\);'
)
_PROVIDER_ENV_STATIC_CLAIM = re.compile(
    rf'static claim\((?P<job>{_ID}),(?P<options>{_ID})\)\{{let '
    rf'(?P<worker>{_ID})=new (?P<class_name>{_ID})\((?P=job),'
    rf'(?P=options)\.spawnPty,(?P=options)\.getAuthSnapshot,"spare",'
    rf'(?P<record>\{{pid:.{{0,1000}}?\.VERSION\}})\);'
)
_PROVIDER_ENV_CLAIM_FRAME = re.compile(
    rf'static buildClaimFrame\((?P<job>{_ID}),(?P<snapshot>{_ID}),'
    rf'(?P<auth>{_ID})\)\{{let (?P<job_dir>{_ID})=(?P<job_dir_fn>{_ID})'
    rf'\((?P=job)\.short\),(?P<env>{_ID})=(?P<env_builder>{_ID})\('
    rf'(?P=job),(?P=job_dir),(?P=snapshot),(?P<rv_sock>{_ID})'
    rf'\((?P=job)\.short\),(?P=auth)\);'
)
_PROVIDER_ENV_DO_SPAWN = re.compile(
    rf'let (?P<argv>{_ID})=(?P<argv_fn>{_ID})\((?P<job>{_ID}),this\.attempt,'
    rf'(?P<has_messages>{_ID}),(?P<session>{_ID}),(?P<flags>{_ID})\),'
    rf'(?P<env>{_ID})=(?P<env_builder>{_ID})\((?P=job),(?P<job_dir>{_ID}),'
    rf'(?P<snapshot>{_ID}),this\.rvSockPath\?\?(?P<rv_sock>{_ID})'
    rf'\((?P=job)\.short\),this\.socketAuth\(\)\);'
)
_PROVIDER_ENV_CLAIM_CALL = re.compile(
    rf'(?P<claim>{_ID})\((?P<job>{_ID}),(?P<spare>{_ID}),(?P<spawn>{_ID}),'
    rf'(?P<auth>{_ID})\)\{{let (?P<worker>{_ID})=(?P<class_name>{_ID})\.claim'
    rf'\((?P=job),(?P<options>\{{pid:(?P=spare)\.hostPid,.{{0,300}}?\}})\);'
)
_PROVIDER_ENV_BUILD_CLAIM_CALL = re.compile(
    rf'function (?P<frame>{_ID})\((?P<job>{_ID}),(?P<snapshot>{_ID}),'
    rf'(?P<auth>{_ID}),(?P<claim_auth>{_ID})\)\{{let\{{env:(?P<env>{_ID}),'
    rf'argv:(?P<argv>{_ID})\}}=(?P<class_name>{_ID})\.buildClaimFrame\('
    rf'(?P=job),(?P=snapshot),(?P=auth)\);'
)
_PROVIDER_ENV_CLAIMED_SPARE_FRAME = re.compile(
    rf'function (?P<claim>{_ID})\((?P<job>{_ID}),(?P<spare>{_ID}),'
    rf'(?P<spawn>{_ID}),(?P<auth>{_ID})\)\{{let (?P<worker>{_ID})='
    rf'(?P<class_name>{_ID})\.claim\((?P=job),(?P<options>\{{.{{0,500}}?\}})\);'
    rf'return (?P<snapshot>{_ID})\((?P=job)\.short,(?P=auth)\?\.\(\)\)\.then\('
    rf'\((?P<snapshot_arg>{_ID})\)=>(?P<send>{_ID})\((?P=spare)\.claimSock,'
    rf'(?P<frame>{_ID})\((?P=job),(?P=snapshot_arg),(?P=worker)\.socketAuth\(\),'
    rf'(?P=spare)\.claimAuth\)\)\)'
)
_PROVIDER_ENV_MANAGER_DISPATCH = re.compile(
    rf'(?P<dispatch>{_ID})=async\((?P<job>{_ID}),(?P<retry>{_ID})=0,'
    rf'(?P<after_upgrade>{_ID})\)=>\{{'
)
_PROVIDER_ENV_MANAGER_RETRY = re.compile(
    rf'return await (?P<delay>{_ID})\(100\),(?P<dispatch>{_ID})\('
    rf'(?P<job>{_ID}),(?P<retry>{_ID})\+1,(?P<after_upgrade>{_ID})\)'
)
_PROVIDER_ENV_MANAGER_CLAIM = re.compile(
    rf'let (?P<worker>{_ID})=(?P<claim>{_ID})\((?P<job>{_ID}),'
    rf'(?P<spare>{_ID}),(?P<spawn>{_ID}),(?P<auth_obj>{_ID})\.getAuthSnapshot\)'
)
_PROVIDER_ENV_MANAGER_SPAWN = re.compile(
    rf'(?P<class_name>{_ID})\.spawn\((?P<job>{_ID}),(?P<spawn>{_ID}),'
    rf'(?P<auth_obj>{_ID})\.getAuthSnapshot,(?P<after_upgrade>{_ID})\?'
    rf'\{{afterUpgrade:(?P=after_upgrade)\}}:void 0\)'
)
_PROVIDER_ENV_CLAIMED_ENTRY = re.compile(
    rf'(?P<prefix>async function (?P<entry>{_ID})\((?P<claim>{_ID}),(?P<main>{_ID})\)'
    rf'\{{.{{0,1000}}?Object\.assign\(process\.env,(?P=claim)\.env\),'
    rf'process\.argv=.{{0,300}}?),(?P<initializers>(?P<cache>{_ID})\(\),'
    rf'(?P<auth>{_ID})\(\),.{{0,300}}?);let\{{main:(?P<worker_main>{_ID})\}}='
    rf'await (?P=main);await (?P=worker_main)\(\)\}}'
)
_PROVIDER_ENV_PREACTION_START = re.compile(
    rf'(?P<hook>\.hook\("preAction",async\((?P<command>{_ID}),(?P<action>{_ID})\)'
    rf'=>\{{(?P<marker>{_ID})\("preAction_start"\);)'
)
_PROVIDER_ENV_PREACTION_INITIALIZED = re.compile(
    rf'await (?P<initializer>{_ID})\(\),(?P<marker>{_ID})\("preAction_after_init"\)'
)
_PROVIDER_ENV_OPERATIONAL_ENTRY = re.compile(
    rf'(?P<guard>if\((?P<noninteractive>{_ID})\)\{{.{{0,300}}?)(?P<settings>{_ID})\(\),'
    rf'(?P<telemetry>{_ID})\(\);let (?P<start>{_ID})=performance\.now\(\),'
)
_PROVIDER_ENV_DELAYED_SETTINGS = re.compile(
    rf'function (?P<telemetry>{_ID})\(\)\{{(?P<prefix>.{{0,1000}}?Waiting for remote '
    rf'managed settings before telemetry init"\),)(?P<wait>{_ID})\(\)\.then\(async\(\)=>'
    rf'\{{(?P<loaded>.{{0,300}}?Remote managed settings loaded, initializing telemetry"\),)'
    rf'(?P<settings>{_ID})\(\),await (?P<initialize>{_ID})\(\)'
)
_PROVIDER_ENV_STATE_WRITE = re.compile(
    rf'async function (?P<write>{_ID})\((?P<dir>{_ID}),(?P<state>{_ID})\)\{{let\{{'
    rf'pinned:(?P<pinned>{_ID}),sortOrder:(?P<sort>{_ID}),stateSortOrder:'
    rf'(?P<state_sort>{_ID}),\.\.\.(?P<rest>{_ID})\}}=(?P=state);'
)
_PROVIDER_ENV_STATE_VIEW = re.compile(
    rf'bgIsolation:(?P<job>{_ID})\.bgIsolation,providerEnv:(?P=job)\.providerEnv,'
)
_PROVIDER_ENV_STATE_SCHEMA = re.compile(
    rf'providerEnv:(?P<schema>{_ID})\.record\((?P=schema)\.string\(\),'
    rf'(?P=schema)\.string\(\)\)\.transform\((?P<filter>{_ID})\)\.optional\(\),'
)
_PROVIDER_ENV_JOB_COPY = re.compile(rf'providerEnv:(?P<prior>{_ID})\?\.providerEnv,')
_PROVIDER_ENV_SEED_STATE = re.compile(
    rf'providerEnv:(?P<snapshot>{_ID})\(\),sessionPermissionRules:'
)
_PROVIDER_ENV_RESPAWN_GUARD = re.compile(r'\|\|(?P<job>[\w$]+)\.providerEnv')
_PROVIDER_ENV_RESPAWN_OPTION = re.compile(
    rf',\.\.\.(?P<job>{_ID})\.providerEnv&&\{{providerEnv:(?P=job)\.providerEnv\}}'
)


def _provider_groups(source: str) -> re.Match[str]:
    groups = _PROVIDER_ENV_GROUPS.search(source)
    if groups is None:
        raise PatchError("background-provider-environment: provider groups absent")
    return groups


_PROVIDER_ENV_VERTEX_REGION_KEYS = (
    "VERTEX_REGION_CLAUDE_FABLE_5",
    "VERTEX_REGION_CLAUDE_HAIKU_4_5",
    "VERTEX_REGION_CLAUDE_3_5_HAIKU",
    "VERTEX_REGION_CLAUDE_3_5_SONNET",
    "VERTEX_REGION_CLAUDE_3_7_SONNET",
    "VERTEX_REGION_CLAUDE_4_8_OPUS",
    "VERTEX_REGION_CLAUDE_4_7_OPUS",
    "VERTEX_REGION_CLAUDE_4_6_OPUS",
    "VERTEX_REGION_CLAUDE_4_5_OPUS",
    "VERTEX_REGION_CLAUDE_4_1_OPUS",
    "VERTEX_REGION_CLAUDE_4_0_OPUS",
    "VERTEX_REGION_CLAUDE_4_6_SONNET",
    "VERTEX_REGION_CLAUDE_4_5_SONNET",
    "VERTEX_REGION_CLAUDE_4_0_SONNET",
)
_PROVIDER_ENV_EXPLICIT_KEYS = (
    "CLAUDE_CONFIG_DIR",
    "CLAUDE_INTERNAL_FC_OVERRIDES",
    "AWS_REGION",
    "AWS_DEFAULT_REGION",
    "AWS_PROFILE",
    "AWS_CONFIG_FILE",
    "AWS_SHARED_CREDENTIALS_FILE",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "GOOGLE_CLOUD_PROJECT",
    "GCLOUD_PROJECT",
    "CLAUDE_SECURESTORAGE_CONFIG_DIR",
    "ANTHROPIC_UNIX_SOCKET",
    "CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST",
    "CLAUDE_CODE_HOST_AUTH_ENV_VAR",
    "CLAUDE_CODE_SDK_HAS_HOST_AUTH_REFRESH",
    "CLAUDE_CODE_HOST_AUTH_REFRESH_TIMEOUT_MS",
    "ANTHROPIC_BEDROCK_SERVICE_TIER",
    "ANTHROPIC_CUSTOM_HEADERS",
    "CLAUDE_CODE_CERT_STORE",
    "CC_KIMI_AUTH_TOKEN",
    "CC_ZAI_AUTH_TOKEN",
    "CC_MINIMAX_AUTH_TOKEN",
    "CC_OPENAI_PROXY_AUTH_TOKEN",
    "CC_OPENAI_PROXY_EFFECTIVE_URL",
    "CC_OPENAI_AVAILABLE",
    *_PROVIDER_ENV_VERTEX_REGION_KEYS,
)


def _provider_key_sources(source: str) -> tuple[str, ...]:
    groups = _provider_groups(source)
    snapshot = _PROVIDER_ENV_SNAPSHOT.search(source)
    if snapshot is None:
        raise PatchError("background-provider-environment: provider snapshot absent")
    return (
        snapshot.group("allowlist"),
        groups.group("selection"),
        groups.group("base_urls"),
        groups.group("credentials"),
        groups.group("skip_auth"),
        groups.group("models"),
        groups.group("custom_models"),
    )


def _provider_keys_expression(source: str) -> str:
    sources = _provider_key_sources(source)
    explicit = tuple(f'"{key}"' for key in _PROVIDER_ENV_EXPLICIT_KEYS)
    return "[" + ",".join((*[f"...{name}" for name in sources], *explicit)) + "]"


def _replace_provider_snapshot(match: re.Match[str]) -> str:
    snapshot = match.group("snapshot")
    result = match.group("result")
    key = match.group("key")
    value = match.group("value")
    sources = _provider_key_sources(match.string)
    keys = _provider_keys_expression(match.string)
    unavailable = "||".join(f"{source}==null" for source in sources)
    key_error = (
        'Object.assign(Error("Background provider environment key registry is '
        'unavailable"),{code:"EPROVIDERENV"})'
    )
    payload_error = (
        'Object.assign(Error("Background provider environment transient payload is '
        'unavailable; dispatch again from the current Claude Code session"),'
        '{code:"EPROVIDERENV"})'
    )
    invalid_error = (
        'Object.assign(Error("Background provider environment transient payload is '
        'invalid; dispatch again from the current Claude Code session"),'
        '{code:"EPROVIDERENV"})'
    )
    return (
        f"function _ccProviderKeys(){{if({unavailable})throw {key_error};"
        f"return {keys}}}function _ccRequireProviderEnv(_ccProviderEnv){{"
        'if(_ccProviderEnv==null||typeof _ccProviderEnv!=="object"||'
        f"Array.isArray(_ccProviderEnv))throw {payload_error};"
        "let _ccAllowed=new Set(_ccProviderKeys());"
        "for(let[_ccKey,_ccValue]of Object.entries(_ccProviderEnv))"
        "if(!_ccAllowed.has(_ccKey)||(_ccValue!==null&&"
        f'typeof _ccValue!=="string"))throw {invalid_error};'
        "return _ccProviderEnv}"
        "function _ccProviderRetain(_ccProviderEnv){"
        "return Object.freeze({..._ccRequireProviderEnv(_ccProviderEnv)})}"
        "function _ccProviderCaptureTransport(_ccEnv=process.env){let _ccSerialized="
        "_ccEnv.CLAUDE_CODE_PROVIDER_ENV_TRANSIENT;delete "
        "_ccEnv.CLAUDE_CODE_PROVIDER_ENV_TRANSIENT;if(_ccSerialized===void 0)"
        "return null;let _ccProviderEnv;try{_ccProviderEnv=JSON.parse(_ccSerialized)}"
        'catch{throw Object.assign(Error("Background provider environment transient '
        'transport is invalid; dispatch again from the current Claude Code session"),'
        '{code:"EPROVIDERENV"})}return _ccProviderRetain(_ccProviderEnv)}'
        "let _ccProviderWorkerEnv=_ccProviderCaptureTransport();"
        "function _ccProviderApplyWorkerFinal(){if(_ccProviderWorkerEnv!==null)"
        "return _ccProviderApplyFinal(_ccProviderWorkerEnv);if(process.env."
        'CLAUDE_CODE_SESSION_KIND==="bg")throw Object.assign(Error("Background provider '
        "environment transient payload is unavailable; dispatch again from the current "
        'Claude Code session"),{code:"EPROVIDERENV"})}'
        "function _ccProviderApplyFinal(_ccProviderEnv){"
        "_ccProviderEnv=_ccRequireProviderEnv(_ccProviderEnv);"
        "for(let _ccKey of _ccProviderKeys())delete process.env[_ccKey];"
        "for(let[_ccKey,_ccValue]of Object.entries(_ccProviderEnv))"
        "if(_ccValue!==null)process.env[_ccKey]=_ccValue}"
        f"function {snapshot}(){{let {result}={{}};"
        f"for(let {key} of _ccProviderKeys()){{let {value}=process.env[{key}];"
        f"{result}[{key}]={value}===void 0?null:{value}}}return {result}}}"
    )


def _replace_provider_schema(match: re.Match[str]) -> str:
    schema = match.group("schema")
    replacement = (
        f"providerEnvVersion:{schema}.number().optional(),"
        f"providerEnv:{schema}.record({schema}.enum(_ccProviderKeys()),"
        f"{schema}.union([{schema}.string(),{schema}.null()])).optional(),"
        "timeoutMs:"
    )
    return match.group(0).replace("timeoutMs:", replacement)


def _replace_provider_socket(match: re.Match[str]) -> str:
    snapshot = _PROVIDER_ENV_PATCHED_SNAPSHOT.search(match.string)
    if snapshot is None:
        raise PatchError("background-provider-environment: snapshot function absent")
    version = _PROVIDER_ENV_PROTOCOL_VERSION
    return (
        f'{match.group("call")}({{proto:{match.group("proto")},op:"dispatch",'
        f'd:{{...{match.group("job")},nonce:{match.group("nonce")}}},'
        f'providerEnvVersion:{version},providerEnv:{snapshot.group("snapshot")}(),'
        f'timeoutMs:5000,auth:await {match.group("auth")}()}}'
    )


def _provider_protocol_error() -> str:
    return (
        '{ok:!1,error:"Background provider environment protocol mismatch. Restart '
        'the stale Claude Code daemon and try again",code:"EPROVIDERENV"}'
    )


def _validated_provider_dispatch_response(response: str, success: str) -> str:
    version = _PROVIDER_ENV_PROTOCOL_VERSION
    return (
        f'if({response}.ok&&{response}.op==="dispatch"){{'
        f'if({response}.providerEnvVersion!=={version})'
        f"{response}={_provider_protocol_error()};else {success}}}"
    )


def _replace_provider_socket_result(match: re.Match[str]) -> str:
    response = match.group("response")
    stale_error = (
        f'if("code"in {response}&&{response}.code==="EPROVIDERENV")'
        f'return{{ok:!1,reason:"daemon-unreachable",detail:{response}.error}};'
    )
    validated_success = _validated_provider_dispatch_response(
        response, match.group("success")
    )
    return validated_success + ";" + stale_error + match.group(0).split(";", 1)[1]


def _replace_provider_redispatch_result(match: re.Match[str]) -> str:
    response = match.group("response")
    validated_success = _validated_provider_dispatch_response(
        response, match.group("success")
    )
    protocol_error = (
        f'if("code"in {response}&&{response}.code==="EPROVIDERENV")'
        f'throw Object.assign(Error({response}.error),{{code:"EPROVIDERENV"}});'
    )
    return validated_success + ";" + protocol_error


def _replace_provider_daemon_ack(match: re.Match[str]) -> str:
    return match.group(0).replace(
        f'op:{match.group("op")},',
        f'op:{match.group("op")},providerEnvVersion:{_PROVIDER_ENV_PROTOCOL_VERSION},',
    )


def _replace_provider_control(match: re.Match[str]) -> str:
    request = match.group("request")
    respond = match.group("respond")
    socket = match.group("socket")
    version = _PROVIDER_ENV_PROTOCOL_VERSION
    capability_check = (
        f'if({request}.providerEnvVersion!=={version}||{request}.providerEnv===void 0)'
        f'return {respond}({socket},{{ok:!1,error:"Background provider environment '
        'protocol mismatch. Restart the stale Claude Code daemon and try again",'
        'code:"EPROVIDERENV"});'
    )
    original = match.group(0)
    original = original.replace(
        f"if(await {match.group('yield')}(0)",
        f"{capability_check}if(await {match.group('yield')}(0)",
    )
    return original.replace(
        f'{match.group("dispatch_cb")}({request}.d)',
        f'{match.group("dispatch_cb")}({request}.d,0,void 0,{request}.providerEnv)',
    )


def _replace_provider_worker(match: re.Match[str]) -> str:
    env = match.group("env")
    prefix = match.group(0)[: -len("if(process.env.")].replace(
        f'{match.group("socket_auth")}){{',
        f'{match.group("socket_auth")},_ccProviderEnv){{',
    )
    return (
        f"{prefix}let _ccProviderPayload="
        "_ccProviderRetain(_ccProviderEnv);"
        f'{env}.CLAUDE_CODE_PROVIDER_ENV_TRANSIENT='
        "JSON.stringify(_ccProviderPayload);"
        f"for(let _ccKey of _ccProviderKeys())delete {env}[_ccKey];"
        f"for(let[_ccKey,_ccValue]of Object.entries(_ccProviderPayload))"
        f"if(_ccValue!==null){env}[_ccKey]=_ccValue;if(process.env."
    )


def _replace_provider_worker_final(match: re.Match[str]) -> str:
    env = match.group("env")
    return (
        match.group("prefix")
        + f"for(let _ccKey of _ccProviderKeys())delete {env}[_ccKey];"
        + "for(let[_ccKey,_ccValue]of Object.entries(_ccProviderPayload))"
        + f"if(_ccValue!==null){env}[_ccKey]=_ccValue;return {env}}}"
    )


def _provider_final_apply(payload: str) -> str:
    if payload == "_ccProviderWorkerEnv":
        return "_ccProviderApplyWorkerFinal();"
    return f"_ccProviderApplyFinal({payload});"


def _replace_provider_claimed_entry(match: re.Match[str]) -> str:
    prefix = match.group("prefix").replace(
        "{",
        f"{{_ccProviderWorkerEnv=_ccProviderCaptureTransport({match.group('claim')}.env);",
        1,
    )
    return (
        prefix
        + ","
        + match.group("initializers")
        + f";let{{main:{match.group('worker_main')}}}=await {match.group('main')};"
        + _provider_final_apply("_ccProviderWorkerEnv")
        + f"await {match.group('worker_main')}()}}"
    )


def _replace_provider_preact_start(match: re.Match[str]) -> str:
    return match.group("hook")


def _replace_provider_preact_initialized(match: re.Match[str]) -> str:
    return match.group(0) + ",_ccProviderApplyWorkerFinal()"


def _replace_provider_operational_entry(match: re.Match[str]) -> str:
    return (
        match.group("guard")
        + f"{match.group('settings')}(),{match.group('telemetry')}(_ccProviderWorkerEnv);"
        + "_ccProviderApplyWorkerFinal();"
        + f"let {match.group('start')}=performance.now(),"
    )


def _replace_provider_delayed_settings(match: re.Match[str]) -> str:
    return (
        f"function {match.group('telemetry')}(_ccProviderWorkerEnv){{{match.group('prefix')}"
        f"{match.group('wait')}().then(async()=>{{{match.group('loaded')}"
        f"{match.group('settings')}(),_ccProviderApplyWorkerFinal(),"
        f"await {match.group('initialize')}()"
    )


def _replace_provider_constructor(match: re.Match[str]) -> str:
    return (
        f'constructor({match.group("job")},{match.group("spawn")},'
        f'{match.group("auth")},{match.group("via")},{match.group("record")},'
        f'_ccProviderEnv){{this.providerEnv=_ccProviderRetain(_ccProviderEnv);this.dispatch='
        f'{match.group("job")};'
    )


def _replace_provider_claimed_spare_frame(match: re.Match[str]) -> str:
    original = match.group(0)
    original = original.replace(
        f'{match.group("auth")}){{', f'{match.group("auth")},_ccProviderEnv){{'
    )
    original = original.replace(
        f'{match.group("options")});', f'{match.group("options")},_ccProviderEnv);'
    )
    return original.replace(
        f'{match.group("spare")}.claimAuth))',
        f'{match.group("spare")}.claimAuth,_ccProviderEnv))',
    )


def _replace_provider_manager_dispatch(match: re.Match[str]) -> str:
    original = match.group(0).replace(
        f'{match.group("after_upgrade")})=>',
        f'{match.group("after_upgrade")},_ccProviderEnv)=>',
    )
    return original + "_ccProviderEnv=_ccProviderRetain(_ccProviderEnv);"


BACKGROUND_PROVIDER_ENV = PatchSet(
    name="background-provider-environment",
    patches=(
        Patch(
            "snapshot-transient-provider-env",
            _PROVIDER_ENV_SNAPSHOT,
            _replace_provider_snapshot,
        ),
        Patch(
            "stop-persisting-provider-env",
            _PROVIDER_ENV_PERSISTED_DEFAULT,
            lambda match: (
                f'{match.group("isolation")}={match.group("source")}==="repl"?'
                f'"none":{match.group("options")}?.bgIsolation,'
                f'{match.group("provider")}=void 0,'
            ),
        ),
        Patch(
            "remove-provider-env-from-state-writes",
            _PROVIDER_ENV_STATE_WRITE,
            lambda match: match.group(0).replace(
                f'...{match.group("rest")}',
                f'providerEnv:_ccProviderEnv,...{match.group("rest")}',
            ),
        ),
        Patch(
            "remove-provider-env-from-state-view",
            _PROVIDER_ENV_STATE_VIEW,
            "bgIsolation:" + r"\g<job>" + ".bgIsolation,",
        ),
        Patch("remove-provider-env-from-state-schema", _PROVIDER_ENV_STATE_SCHEMA, ""),
        Patch("remove-provider-env-from-job-copy", _PROVIDER_ENV_JOB_COPY, ""),
        Patch(
            "remove-provider-env-from-seed-state",
            _PROVIDER_ENV_SEED_STATE,
            "sessionPermissionRules:",
        ),
        Patch(
            "remove-provider-env-from-respawn-guard", _PROVIDER_ENV_RESPAWN_GUARD, ""
        ),
        Patch(
            "remove-provider-env-from-respawn-options", _PROVIDER_ENV_RESPAWN_OPTION, ""
        ),
        Patch(
            "add-control-provider-env", _PROVIDER_ENV_SCHEMA, _replace_provider_schema
        ),
        Patch(
            "send-provider-env-over-socket",
            _PROVIDER_ENV_SOCKET,
            _replace_provider_socket,
        ),
        Patch(
            "reject-stale-daemon-without-file-fallback",
            _PROVIDER_ENV_SOCKET_RESULT,
            _replace_provider_socket_result,
        ),
        Patch(
            "validate-ack-timeout-redispatch",
            _PROVIDER_ENV_REDISPATCH_RESULT,
            _replace_provider_redispatch_result,
        ),
        Patch(
            "acknowledge-provider-env-version",
            _PROVIDER_ENV_DAEMON_ACK,
            _replace_provider_daemon_ack,
        ),
        Patch(
            "pass-provider-env-to-manager",
            _PROVIDER_ENV_CONTROL,
            _replace_provider_control,
        ),
        Patch(
            "declare-worker-provider-env",
            _PROVIDER_ENV_MANAGER,
            lambda match: match.group(0).replace("dispatch;", "dispatch;providerEnv;"),
        ),
        Patch(
            "store-worker-provider-env",
            _PROVIDER_ENV_CONSTRUCTOR,
            _replace_provider_constructor,
        ),
        Patch(
            "thread-provider-env-through-spawn",
            _PROVIDER_ENV_STATIC_SPAWN,
            lambda match: (
                match.group(0)
                .replace('"cold")', '"cold",_ccProviderEnv)')
                .replace(
                    f'{match.group("options")})',
                    f'{match.group("options")},_ccProviderEnv)',
                )
            ),
        ),
        Patch(
            "thread-provider-env-through-claim",
            _PROVIDER_ENV_STATIC_CLAIM,
            lambda match: (
                f'static claim({match.group("job")},{match.group("options")},_ccProviderEnv){{let {match.group("worker")}=new {match.group("class_name")}({match.group("job")},{match.group("options")}.spawnPty,{match.group("options")}.getAuthSnapshot,"spare",{match.group("record")},_ccProviderEnv);'
            ),
        ),
        Patch(
            "apply-provider-env-to-claim-frame",
            _PROVIDER_ENV_CLAIM_FRAME,
            lambda match: (
                match.group(0)
                .replace(
                    f'{match.group("auth")})', f'{match.group("auth")},_ccProviderEnv)'
                )
                .replace(
                    f'{match.group("auth")}){{',
                    f'{match.group("auth")},_ccProviderEnv){{',
                )
            ),
        ),
        Patch(
            "apply-provider-env-to-respawns",
            _PROVIDER_ENV_DO_SPAWN,
            lambda match: match.group(0).replace(
                "this.socketAuth());", "this.socketAuth(),this.providerEnv);"
            ),
        ),
        Patch(
            "thread-provider-env-through-claimed-spare-frame",
            _PROVIDER_ENV_CLAIMED_SPARE_FRAME,
            _replace_provider_claimed_spare_frame,
        ),
        Patch(
            "thread-provider-env-to-claim-frame",
            _PROVIDER_ENV_BUILD_CLAIM_CALL,
            lambda match: (
                match.group(0)
                .replace(
                    f'{match.group("claim_auth")}){{',
                    f'{match.group("claim_auth")},_ccProviderEnv){{',
                )
                .replace(
                    f'{match.group("auth")});',
                    f'{match.group("auth")},_ccProviderEnv);',
                )
            ),
        ),
        Patch(
            "accept-transient-provider-env-in-manager",
            _PROVIDER_ENV_MANAGER_DISPATCH,
            _replace_provider_manager_dispatch,
        ),
        Patch(
            "retain-provider-env-while-worker-settles",
            _PROVIDER_ENV_MANAGER_RETRY,
            lambda match: match.group(0)[:-1] + ",_ccProviderEnv)",
        ),
        Patch(
            "pass-provider-env-to-claimed-spare",
            _PROVIDER_ENV_MANAGER_CLAIM,
            lambda match: f'{match.group(0)[:-1]},_ccProviderEnv)',
        ),
        Patch(
            "pass-provider-env-to-cold-worker",
            _PROVIDER_ENV_MANAGER_SPAWN,
            lambda match: f'{match.group(0)[:-1]},_ccProviderEnv)',
        ),
        Patch(
            "scrub-worker-provider-env", _PROVIDER_ENV_WORKER, _replace_provider_worker
        ),
        Patch(
            "restore-provider-env-after-native-worker-scrubs",
            _PROVIDER_ENV_WORKER_FINAL,
            _replace_provider_worker_final,
        ),
        Patch(
            "apply-provider-env-after-claimed-initializers",
            _PROVIDER_ENV_CLAIMED_ENTRY,
            _replace_provider_claimed_entry,
        ),
        Patch(
            "capture-worker-provider-env-before-settings",
            _PROVIDER_ENV_PREACTION_START,
            _replace_provider_preact_start,
        ),
        Patch(
            "restore-provider-env-after-settings-initializer",
            _PROVIDER_ENV_PREACTION_INITIALIZED,
            _replace_provider_preact_initialized,
        ),
        Patch(
            "restore-provider-env-at-operational-entry",
            _PROVIDER_ENV_OPERATIONAL_ENTRY,
            _replace_provider_operational_entry,
        ),
        Patch(
            "restore-provider-env-after-delayed-settings",
            _PROVIDER_ENV_DELAYED_SETTINGS,
            _replace_provider_delayed_settings,
        ),
    ),
    verify_present=(
        re.compile(r'function _ccProviderKeys\(\)\{if\('),
        re.compile(r'function _ccRequireProviderEnv\('),
        re.compile(r'function _ccProviderRetain\('),
        re.compile(r'let _ccProviderWorkerEnv=_ccProviderCaptureTransport\(\)'),
        re.compile(r'providerEnvVersion:3,providerEnv:[\w$]+\(\),timeoutMs:5000'),
        re.compile(r'op:[\w$]+,providerEnvVersion:3,short:'),
        re.compile(r'[\w$]+\.providerEnvVersion!==3'),
        re.compile(
            r'throw Object\.assign\(Error\([\w$]+\.error\),\{code:"EPROVIDERENV"\}\)'
        ),
        re.compile(r'"CLAUDE_CODE_CERT_STORE"'),
        re.compile(r'"VERTEX_REGION_CLAUDE_FABLE_5"'),
        re.compile(r'"VERTEX_REGION_CLAUDE_4_8_OPUS"'),
        re.compile(r'"VERTEX_REGION_CLAUDE_4_0_SONNET"'),
        re.compile(r'protocol mismatch\. Restart the stale Claude Code daemon'),
        re.compile(r'for\(let _ccKey of _ccProviderKeys\(\)\)delete process\.env'),
        re.compile(r'this\.providerEnv=_ccProviderRetain\(_ccProviderEnv\)'),
        re.compile(r'providerEnv:_ccProviderEnv,\.\.\.[\w$]+'),
    ),
    verify_absent=(
        re.compile(r'providerEnv:[\w$]+\?\.providerEnv'),
        re.compile(r'providerEnv:[\w$]+\(\),sessionPermissionRules'),
        re.compile(r'\.providerEnv&&\{providerEnv:'),
        re.compile(r'providerEnv:[\w$]+\.record\([\w$]+\.string\(\),[\w$]+\.string'),
        re.compile(r'_ccProviderSnapshotFromEnv'),
    ),
    min_version=_V_2_1_174,
    max_version=(2, 1, 175),
    requires_version=True,
)

# --- in-process multi-provider Anthropic SDK routing (2.1.174 only) ----------

_MODEL_COSTS_RE = re.compile(
    r"(\},[\w$]+=[\w$]+;[\w$]+=\{)(\[[\w$]+\([\w$]+\.firstParty\)\]:)"
)


def _model_costs_patch(model_costs: ModelCostsByModel) -> Patch:
    # Claude Code computes statusline/session cost from its own per-model table.
    # Claude Code lowercases model IDs before cost lookup. Use normalized qualified IDs.
    table = ",".join(
        f"{json.dumps(model.lower(), separators=(',', ':'))}:"
        f"{{inputTokens:{costs['inputTokens']},"
        f"outputTokens:{costs['outputTokens']},"
        f"promptCacheWriteTokens:{costs['promptCacheWriteTokens']},"
        f"promptCacheReadTokens:{costs['promptCacheReadTokens']},"
        f"webSearchRequests:{costs['webSearchRequests']}}}"
        for model, costs in model_costs.items()
    )

    def repl(match: re.Match[str]) -> str:
        return f"{match.group(1)}{table},{match.group(2)}"

    return Patch(name="model-costs", pattern=_MODEL_COSTS_RE, replacement=repl)


_MULTI_PROVIDER_CATALOG_SOURCE: tuple[MultiProviderDefinition, ...] = (
    {
        "provider": "kimi",
        "attributionDomain": "kimi.com",
        "baseURL": "https://api.kimi.com/coding",
        "tokenEnv": "CC_KIMI_AUTH_TOKEN",
        "defaultHeaders": {"User-Agent": "KimiCLI/1.5"},
        "models": (
            {
                "wireModel": "kimi-k3",
                "label": "Kimi K3",
                "description": "Kimi general-purpose model",
                "contextWindow": 1_048_576,
                "maxOutputTokens": 131_072,
                "costs": {
                    "inputTokens": 3,
                    "outputTokens": 15,
                    # Accounting assumption: charge cache writes at the ordinary
                    # input-token rate because Kimi publishes cache-hit/cache-miss
                    # prices rather than a separate cache-write price.
                    # Source: https://platform.kimi.ai/docs/pricing/chat-k3.md
                    "promptCacheWriteTokens": 3,
                    "promptCacheReadTokens": 0.3,
                    "webSearchRequests": 0,
                },
            },
            {
                "wireModel": "kimi-k2.7-code",
                "label": "Kimi K2.7 Code",
                "description": "Kimi coding model",
                "contextWindow": 262_144,
                "maxOutputTokens": 32_768,
                "costs": {
                    "inputTokens": 0.95,
                    "outputTokens": 4,
                    "promptCacheWriteTokens": 0.95,
                    "promptCacheReadTokens": 0.19,
                    "webSearchRequests": 0,
                },
            },
        ),
    },
    {
        "provider": "zai",
        "attributionDomain": "z.ai",
        "baseURL": "https://api.z.ai/api/anthropic",
        "tokenEnv": "CC_ZAI_AUTH_TOKEN",
        "defaultHeaders": {},
        "models": (
            {
                "wireModel": "glm-5.3",
                "label": "GLM 5.3",
                "description": "Z.ai flagship model",
                "contextWindow": 1_000_000,
                "maxOutputTokens": 131_072,
                "costs": {
                    "inputTokens": 1.4,
                    "outputTokens": 4.4,
                    "promptCacheWriteTokens": 1.4,
                    "promptCacheReadTokens": 0.26,
                    "webSearchRequests": 0,
                },
            },
            {
                "wireModel": "glm-5.3-flash",
                "label": "GLM 5.3 Flash",
                "description": "Z.ai fast model",
                "contextWindow": 1_000_000,
                "maxOutputTokens": 131_072,
                # Use the standard rate effective on 2026-09-10, after the launch
                # promotion ends. Source: https://docs.z.ai/guides/overview/pricing
                "costs": {
                    "inputTokens": 0.15,
                    "outputTokens": 0.5,
                    "promptCacheWriteTokens": 0.15,
                    "promptCacheReadTokens": 0.03,
                    "webSearchRequests": 0,
                },
            },
            {
                "wireModel": "glm-5.2",
                "label": "GLM 5.2",
                "description": "Z.ai coding model",
                "contextWindow": 1_000_000,
                "maxOutputTokens": 131_072,
                "costs": {
                    "inputTokens": 1.4,
                    "outputTokens": 4.4,
                    "promptCacheWriteTokens": 1.4,
                    "promptCacheReadTokens": 0.26,
                    "webSearchRequests": 0,
                },
            },
            {
                "wireModel": "glm-5-turbo",
                "label": "GLM 5 Turbo",
                "description": "Z.ai coding model",
                "contextWindow": 200_000,
                "maxOutputTokens": 131_072,
                "costs": {
                    "inputTokens": 1.2,
                    "outputTokens": 4,
                    "promptCacheWriteTokens": 1.2,
                    "promptCacheReadTokens": 0.2,
                    "webSearchRequests": 0,
                },
            },
            {
                "wireModel": "glm-4.7",
                "label": "GLM 4.7",
                "description": "Z.ai coding model",
                "contextWindow": 204_800,
                "maxOutputTokens": 131_072,
                "costs": {
                    "inputTokens": 0.6,
                    "outputTokens": 2.2,
                    "promptCacheWriteTokens": 0.6,
                    "promptCacheReadTokens": 0.11,
                    "webSearchRequests": 0,
                },
            },
            {
                "wireModel": "glm-4.5-air",
                "label": "GLM 4.5 Air",
                "description": "Z.ai coding model",
                "contextWindow": 131_072,
                "maxOutputTokens": 98_304,
                "costs": {
                    "inputTokens": 0.2,
                    "outputTokens": 1.1,
                    "promptCacheWriteTokens": 0.2,
                    "promptCacheReadTokens": 0.03,
                    "webSearchRequests": 0,
                },
            },
        ),
    },
    {
        "provider": "minimax",
        "attributionDomain": "minimax.io",
        "baseURL": "https://api.minimax.io/anthropic",
        "tokenEnv": "CC_MINIMAX_AUTH_TOKEN",
        "defaultHeaders": {},
        "models": (
            {
                "wireModel": "MiniMax-M3",
                "label": "MiniMax M3",
                "description": "MiniMax coding model",
                "contextWindow": 1_048_576,
                "maxOutputTokens": 512_000,
                "costs": {
                    "inputTokens": 0.3,
                    "outputTokens": 1.2,
                    "promptCacheWriteTokens": 0.375,
                    "promptCacheReadTokens": 0.06,
                    "webSearchRequests": 0,
                },
            },
            {
                "wireModel": "MiniMax-M2.7",
                "label": "MiniMax M2.7",
                "description": "MiniMax coding model",
                "contextWindow": 204_800,
                "maxOutputTokens": 131_072,
                "costs": {
                    "inputTokens": 0.3,
                    "outputTokens": 1.2,
                    "promptCacheWriteTokens": 0.375,
                    "promptCacheReadTokens": 0.06,
                    "webSearchRequests": 0,
                },
            },
        ),
    },
    {
        "provider": "openai",
        "attributionDomain": "openai.com",
        "baseURL": "http://127.0.0.1:17780",
        "baseURLEnv": "CC_OPENAI_PROXY_EFFECTIVE_URL",
        "tokenEnv": "CC_OPENAI_PROXY_AUTH_TOKEN",
        "availabilityEnv": "CC_OPENAI_AVAILABLE",
        "defaultHeaders": {},
        "models": (
            {
                "wireModel": "gpt-6-astra",
                "label": "GPT-6 Astra",
                "description": "OpenAI Codex model",
                "contextWindow": 272_000,
                "maxOutputTokens": 128_000,
                "costs": {
                    "inputTokens": 10,
                    "outputTokens": 50,
                    "promptCacheWriteTokens": 12.5,
                    "promptCacheReadTokens": 1,
                    "webSearchRequests": 0.01,
                },
            },
            {
                "wireModel": "gpt-5.6-sol",
                "label": "GPT-5.6 Sol",
                "description": "OpenAI Codex model",
                "contextWindow": 272_000,
                "maxOutputTokens": 128_000,
                "costs": {
                    "inputTokens": 4,
                    "outputTokens": 20,
                    "promptCacheWriteTokens": 5,
                    "promptCacheReadTokens": 0.4,
                    "webSearchRequests": 0.01,
                },
            },
            {
                "wireModel": "gpt-5.6-terra",
                "label": "GPT-5.6 Terra",
                "description": "OpenAI Codex model",
                "contextWindow": 272_000,
                "maxOutputTokens": 128_000,
                "costs": {
                    "inputTokens": 2,
                    "outputTokens": 12,
                    "promptCacheWriteTokens": 2.5,
                    "promptCacheReadTokens": 0.2,
                    "webSearchRequests": 0.01,
                },
            },
            {
                "wireModel": "gpt-5.6-luna",
                "label": "GPT-5.6 Luna",
                "description": "OpenAI Codex model",
                "contextWindow": 272_000,
                "maxOutputTokens": 128_000,
                "costs": {
                    "inputTokens": 0.2,
                    "outputTokens": 1.2,
                    "promptCacheWriteTokens": 0.25,
                    "promptCacheReadTokens": 0.02,
                    "webSearchRequests": 0.01,
                },
            },
        ),
    },
)
_MULTI_PROVIDER_DEFINITIONS = json.dumps(
    {
        definition["provider"]: {
            "attributionDomain": definition["attributionDomain"],
            "baseURL": definition["baseURL"],
            "tokenEnv": definition["tokenEnv"],
            **(
                {"availabilityEnv": definition["availabilityEnv"]}
                if "availabilityEnv" in definition
                else {}
            ),
            **(
                {"baseURLEnv": definition["baseURLEnv"]}
                if "baseURLEnv" in definition
                else {}
            ),
            "defaultHeaders": definition["defaultHeaders"],
            "models": [model["wireModel"] for model in definition["models"]],
        }
        for definition in _MULTI_PROVIDER_CATALOG_SOURCE
    },
    separators=(",", ":"),
)
_MULTI_PROVIDER_PREFIXES = json.dumps(
    [definition["provider"] for definition in _MULTI_PROVIDER_CATALOG_SOURCE],
    separators=(",", ":"),
)
_MULTI_PROVIDER_CATALOG = json.dumps(
    [
        {
            "value": f'{definition["provider"]}:{model["wireModel"]}',
            "label": model["label"],
            "attributionDomain": definition["attributionDomain"],
            "description": model["description"],
            "contextWindow": model["contextWindow"],
            "maxOutputTokens": model["maxOutputTokens"],
        }
        for definition in _MULTI_PROVIDER_CATALOG_SOURCE
        for model in definition["models"]
    ],
    separators=(",", ":"),
)
_MULTI_PROVIDER_MODEL_COSTS: ModelCostsByModel = {
    f'{definition["provider"]}:{model["wireModel"]}': model["costs"]
    for definition in _MULTI_PROVIDER_CATALOG_SOURCE
    for model in definition["models"]
}
_MULTI_PROVIDER_HELPER = (
    f"const _ccMultiProviderDefinitions={_MULTI_PROVIDER_DEFINITIONS};"
    f"const _ccMultiProviderCatalog={_MULTI_PROVIDER_CATALOG};"
    f"const _ccMultiProviderPrefixes={_MULTI_PROVIDER_PREFIXES},"
    '_ccMultiProviderDeniedRequestFields=["fallback_credit_token"],'
    '_ccMultiProviderTraceHeaders=["traceparent","tracestate","baggage"],'
    "_ccMultiProviderClients=new Map;"
    "function _ccMultiProviderCatalogInfo(_ccModel){if(typeof _ccModel!==\"string\")"
    "return null;return _ccMultiProviderCatalog.find((_ccEntry)=>_ccEntry.value==="
    "_ccModel)??null}"
    "function _ccMultiProviderAttribution(_ccModel,_ccNativeLabel){let _ccEntry="
    "_ccMultiProviderCatalogInfo(_ccModel);return{label:_ccEntry?.label??_ccNativeLabel,"
    'domain:_ccEntry?.attributionDomain??"anthropic.com"}}'
    "function _ccMultiProviderModelProvider(_ccModel){if(typeof _ccModel!==\"string\")"
    'return"anthropic";let _ccSeparator=_ccModel.indexOf(":"),_ccPrefix='
    "_ccSeparator<0?null:_ccModel.slice(0,_ccSeparator).toLowerCase();if(_ccPrefix&&"
    "_ccMultiProviderPrefixes.includes(_ccPrefix))return _ccPrefix;let _ccKnown="
    "_ccMultiProviderCatalog.find((_ccEntry)=>_ccEntry.value.slice("
    "_ccEntry.value.indexOf(\":\")+1).toLowerCase()===_ccModel.toLowerCase());return "
    '_ccKnown?_ccKnown.value.slice(0,_ccKnown.value.indexOf(":")):"anthropic"}'
    "function _ccMultiProviderModelError(_ccMessage){return Object.assign(Error("
    '_ccMessage),{code:"EPROVIDERMODEL"})}'
    "function _ccMultiProviderModelInfo(_ccModel){if(typeof _ccModel!==\"string\")"
    "return null;let _ccSeparator=_ccModel.indexOf(\":\"),_ccProvider="
    "_ccSeparator<0?null:_ccModel.slice(0,_ccSeparator),_ccWireModel="
    "_ccSeparator<0?_ccModel:_ccModel.slice(_ccSeparator+1),_ccKnownWire="
    "_ccMultiProviderCatalog.find((_ccEntry)=>_ccEntry.value.slice("
    "_ccEntry.value.indexOf(\":\")+1).toLowerCase()===_ccWireModel.toLowerCase()),"
    "_ccCanonicalProvider=_ccProvider===null?null:_ccMultiProviderPrefixes.find("
    "(_ccPrefix)=>_ccPrefix===_ccProvider.toLowerCase());if(_ccProvider===null){if("
    "_ccKnownWire)throw _ccMultiProviderModelError(\"External model requires a "
    "provider prefix: \"+_ccModel);return null}if(_ccCanonicalProvider&&_ccProvider!=="
    "_ccCanonicalProvider)throw _ccMultiProviderModelError(\"Provider prefix must be "
    "lowercase: \"+_ccProvider);if(!_ccCanonicalProvider){if(_ccKnownWire)throw "
    "_ccMultiProviderModelError(\"External model has an unknown provider prefix: \"+"
    "_ccModel);return null}if(!_ccWireModel.trim())throw _ccMultiProviderModelError("
    '"Invalid qualified model: wire model is blank");let _ccDefinition='
    "_ccMultiProviderDefinitions[_ccCanonicalProvider];if(!_ccDefinition.models.includes("
    "_ccWireModel))throw _ccMultiProviderModelError(\"Unknown qualified model: \"+"
    "_ccCanonicalProvider+\":\"+_ccWireModel);return{provider:"
    "_ccCanonicalProvider,wireModel:_ccWireModel,definition:_ccDefinition}}"
    "function _ccMultiProviderAvailable(_ccProvider){let _ccDefinition="
    "_ccMultiProviderDefinitions[_ccProvider],_ccToken=process.env["
    "_ccDefinition.tokenEnv]?.trim(),_ccBaseURL=_ccDefinition.baseURLEnv?process.env["
    "_ccDefinition.baseURLEnv]?.trim():_ccDefinition.baseURL;return!!_ccToken&&"
    "!!_ccBaseURL&&(!_ccDefinition.availabilityEnv||process.env["
    '_ccDefinition.availabilityEnv]==="1")}function '
    "_ccMultiProviderPickerCatalog(){return _ccMultiProviderCatalog.filter("
    "(_ccEntry)=>_ccMultiProviderAvailable(_ccEntry.value.slice(0,"
    '_ccEntry.value.indexOf(":"))))}'
    "function _ccMultiProviderToolAllowed(_ccModel,_ccTool){return _ccTool.isMcp===!0||"
    '_ccTool.name!=="WebSearch"||_ccMultiProviderModelProvider(_ccModel)==="anthropic"}'
    "function _ccMultiProviderSafeFetchOptions(_ccOptions){if(!_ccOptions)return "
    "_ccOptions;let{headers:_ccHeaders,..._ccSafe}=_ccOptions;return _ccSafe}"
    "function _ccMultiProviderSafeOptions(_ccOptions){let _ccSafe={};if("
    'Object.hasOwn(_ccOptions,"signal"))_ccSafe.signal=_ccOptions.signal;if('
    'Object.hasOwn(_ccOptions,"timeout"))_ccSafe.timeout=_ccOptions.timeout;let '
    "_ccHeaders={};for(let[_ccName,_ccValue]of Object.entries(_ccOptions.headers??{}))"
    "if(_ccMultiProviderTraceHeaders.includes(_ccName.toLowerCase()))"
    "_ccHeaders[_ccName]=_ccValue;if(Object.keys(_ccHeaders).length)_ccSafe.headers="
    "_ccHeaders;return _ccSafe}"
    "function _ccMultiProviderCredential(_ccInfo){let _ccToken=process.env["
    "_ccInfo.definition.tokenEnv]?.trim(),_ccBaseURL=_ccInfo.definition.baseURLEnv?"
    "process.env[_ccInfo.definition.baseURLEnv]?.trim():_ccInfo.definition.baseURL;if("
    "!_ccToken||!_ccBaseURL||(_ccInfo.definition.availabilityEnv&&process.env["
    "_ccInfo.definition.availabilityEnv]!==\"1\")){_ccMultiProviderClients.delete("
    "_ccInfo.provider);let _ccMissing=!_ccToken?_ccInfo.definition.tokenEnv:!_ccBaseURL?"
    "_ccInfo.definition.baseURLEnv:_ccInfo.definition.availabilityEnv;throw Object.assign("
    "Error(\"Missing provider configuration: \"+_ccMissing),"
    '{code:"EPROVIDERCREDENTIAL"})}return{token:_ccToken,baseURL:_ccBaseURL}}'
    "function _ccMultiProviderPreflight(_ccModel){let _ccInfo="
    "_ccMultiProviderModelInfo(_ccModel);if(_ccInfo)_ccMultiProviderCredential(_ccInfo)}"
    "function _ccMultiProviderInputTokens(_ccModel,_ccResponse){if("
    "_ccMultiProviderModelInfo(_ccModel)&&typeof _ccResponse?.input_tokens!==\"number\")"
    "throw Object.assign(Error(\"External provider countTokens response must contain "
    'numeric input_tokens"),{code:"EPROVIDERINCOMPATIBLE"});return '
    "_ccResponse?.input_tokens}"
    "function _ccMultiProviderRoute(_ccNativeClient,_ccRequest,_ccOptions={}){let "
    "_ccInfo=_ccMultiProviderModelInfo(_ccRequest.model);if(!_ccInfo)return["
    "_ccNativeClient,_ccRequest,_ccOptions];let{token:_ccToken,baseURL:_ccBaseURL}="
    "_ccMultiProviderCredential(_ccInfo),_ccCached=_ccMultiProviderClients.get("
    "_ccInfo.provider),_ccTransportChanged=!_ccCached||_ccCached.token!==_ccToken||"
    "_ccCached.baseURL!==_ccBaseURL||"
    "_ccCached.nativeClient!==_ccNativeClient||_ccCached.timeout!=="
    "_ccNativeClient.timeout||_ccCached.fetchOptions!==_ccNativeClient.fetchOptions||"
    "_ccCached.fetch!==_ccNativeClient.fetch;if(_ccTransportChanged){let "
    "_ccConstructor=_ccMultiProviderSDK(),_ccClient=new _ccConstructor({baseURL:"
    "_ccBaseURL,apiKey:null,authToken:_ccToken,maxRetries:0,"
    "dangerouslyAllowBrowser:!0,timeout:_ccNativeClient.timeout,fetchOptions:"
    "_ccMultiProviderSafeFetchOptions(_ccNativeClient.fetchOptions),fetch:"
    "_ccNativeClient.fetch,defaultHeaders:{..."
    "_ccInfo.definition.defaultHeaders}});if(_ccClient._options)_ccClient._options={..."
    "_ccClient._options,defaultHeaders:{..._ccInfo.definition.defaultHeaders}};"
    "_ccCached={token:_ccToken,baseURL:_ccBaseURL,nativeClient:_ccNativeClient,timeout:"
    "_ccNativeClient.timeout,fetchOptions:_ccNativeClient.fetchOptions,fetch:"
    "_ccNativeClient.fetch,client:_ccClient};_ccMultiProviderClients.set("
    "_ccInfo.provider,_ccCached)}let _ccOutbound={..._ccRequest,model:"
    "_ccInfo.wireModel};for(let _ccField of _ccMultiProviderDeniedRequestFields)"
    "delete _ccOutbound[_ccField];return[_ccCached.client,_ccOutbound,"
    "_ccMultiProviderSafeOptions(_ccOptions)]}"
)
_MULTI_PROVIDER_RESUME = re.compile(
    rf'let (?P<model>{_ID})=(?P<message>{_ID})\.message\.model,'
    rf'(?P<setting>{_ID})=(?P<current>{_ID})\(\);if\('
    rf'(?P<dependent>{_ID})\((?P=setting)\)&&!(?P<eap>{_ID})\((?P=model)\)&&'
    rf'(?P<compatible>{_ID})\((?P=setting),(?P<normalize>{_ID})\((?P=model)\)\)\)'
    r'return\{kind:"mode_dependent_setting"\};'
)
_MULTI_PROVIDER_AGENT_MODEL = re.compile(
    r'model:(?P<schema>[\w$]+)\.enum\(\["sonnet","opus","haiku","fable"\]\)'
    r'(?=\.optional\(\)\.describe\("Optional model override for this agent\.)'
)


def _restore_multi_provider_model(match: re.Match[str]) -> str:
    model = match.group("model")
    # Only restore bare wire IDs when the catalogue has one matching provider.
    return (
        f"let {model}={match.group('message')}.message.model;"
        f"let _ccCandidates=_ccMultiProviderCatalog.filter((_ccEntry)=>"
        f"_ccEntry.value==={model}||_ccEntry.value.slice("
        f'_ccEntry.value.indexOf(":")+1)==={model});'
        "if(_ccCandidates.length===1){let _ccRestored=_ccCandidates[0].value;"
        'return Ew(_ccRestored)?{kind:"ok",model:_ccRestored}:'
        '{kind:"declined",model:_ccRestored,reason:"not_allowed"}}'
        f"let {match.group('setting')}={match.group('current')}();if("
        f"{match.group('dependent')}({match.group('setting')})&&!"
        f"{match.group('eap')}({model})&&{match.group('compatible')}("
        f"{match.group('setting')},{match.group('normalize')}({model})))"
        'return{kind:"mode_dependent_setting"};'
    )


def _expand_multi_provider_agent_model(match: re.Match[str]) -> str:
    # These native catalogue bindings are specific to Claude Code 2.1.174.
    return (
        f"model:{match.group('schema')}.enum([...new Set([...LyH,...wPK,"
        "...Object.values(N5()),...UX$().filter((_ccEntry)=>"
        'typeof _ccEntry.value==="string").map((_ccEntry)=>_ccEntry.value),'
        "..._ccMultiProviderCatalog.map((_ccEntry)=>_ccEntry.value)])])"
    )


_MULTI_PROVIDER_SDK_TAIL = re.compile(
    rf'(?P<prefix>let (?P<options>{_ID})=\{{apiKey:.{{0,500}}?\}};return new '
    rf'(?P<constructor>{_ID})\((?P=options)\)\}})(?P<next>async function {_ID}\()'
)
_MULTI_PROVIDER_THINKING_FILTER = re.compile(
    rf"function (?P<function>{_ID})\((?P<messages>{_ID}),(?P<model>{_ID})\)"
    rf"\{{return (?P<filter>{_ID})\((?P=messages),\((?P<message>{_ID})\)=>"
    rf"(?P=message)\.message\.model!==(?P<synthetic>{_ID})&&"
    rf"(?P=message)\.message\.model!==(?P=model)\)\}}"
)
_MULTI_PROVIDER_NONSTREAMING = re.compile(
    rf'let (?P<response>{_ID})=await (?P<client>{_ID})\.beta\.messages\.create\('
    rf'(?P<request>\{{\.\.\.(?P<finalized>{_ID}),model:KA\((?P=finalized)\.model\)\}}),'
    rf'(?P<options>\{{signal:(?P<signal>{_ID})\.signal,timeout:(?P<timeout>{_ID}),'
    rf'\.\.\.Object\.keys\((?P<headers>{_ID})\)\.length>0&&\{{headers:(?P=headers)\}}\}})'
)
_MULTI_PROVIDER_STREAMING = re.compile(
    rf'let (?P<response>{_ID})=await (?P<client>{_ID})\.beta\.messages\.create\('
    rf'(?P<request>\{{\.\.\.(?P<finalized>{_ID}),\.\.\.(?P<credit>{_ID})!==void 0&&'
    rf'\{{fallback_credit_token:(?P=credit)\}},stream:!0\}}),'
    rf'(?P<options>\{{signal:(?P<signal>{_ID}),\.\.\.Object\.keys\('
    rf'(?P<headers>{_ID})\)\.length>0&&\{{headers:(?P=headers)\}}\}})'
)
_MULTI_PROVIDER_SIDE_QUERY = re.compile(
    rf'let (?P<started>{_ID})=performance\.now\(\),(?P<response>{_ID})=await '
    rf'(?P<client>{_ID})\.beta\.messages\.create\((?P<request>{_ID}),'
    rf'(?P<options>\{{signal:(?P<signal>{_ID}),\.\.\.(?P<timeout>{_ID})!==void 0&&'
    rf'\{{timeout:(?P=timeout)\}}\}})'
)
_MULTI_PROVIDER_COUNT_TOKENS = re.compile(
    rf'let (?P<client>{_ID})=await LF\(\{{maxRetries:1,model:(?P<model>{_ID}),'
    rf'source:"count_tokens"\}}\),(?P<betas>{_ID})=(?P<raw_betas>{_ID})\.filter\('
    rf'\((?P<beta>{_ID})\)=>ij6\.has\((?P=beta)\)\),(?P<response>{_ID})=await '
    rf'(?P=client)\.beta\.messages\.countTokens\((?P<request>\{{model:KA\('
    rf'(?P=model)\),messages:.{{0,500}}?\}})\)'
)
_MULTI_PROVIDER_COUNT_TOKENS_CATCH = re.compile(
    rf'(?P<prefix>async function (?P<function>{_ID})\((?P<messages>{_ID}),(?P<tools>{_ID}),'
    rf'(?P<model_arg>{_ID})\)\{{return .{{0,200}}?async\(\)=>\{{try\{{.{{0,1800}}?return '
    rf'(?P<response>{_ID})\.input_tokens)\}}catch\((?P<error>{_ID})\)\{{'
    rf'(?P<body>return N\(`countTokens API call failed:.{{0,200}}?null)\}}\}}\)\}}'
)
_MULTI_PROVIDER_PICKER = re.compile(
    rf'function (?P<function>{_ID})\((?P<flag>{_ID})\)\{{let (?P<options>{_ID})='
    rf'(?P<native>{_ID})\((?P=flag)\),(?P<custom>{_ID})=process\.env\.'
    r'ANTHROPIC_CUSTOM_MODEL_OPTION;'
)
_MULTI_PROVIDER_RECOGNITION = re.compile(
    rf'function (?P<function>{_ID})\((?P<model>{_ID})\)\{{let (?P<name>{_ID})='
    rf'(?P<display>{_ID})\((?P=model)\);if\(!(?P=name)\)return null;let '
    rf'(?P<normalized>{_ID})=(?P<normalize>{_ID})\((?P=model)\),(?P<alias>{_ID})=null;'
    r'if\((?P=normalized)\.includes\("fable"\)\)'
)
_MULTI_PROVIDER_TOOL_SCHEMA = re.compile(
    rf'(?P<schemas>{_ID})=await Promise\.all\((?P<tools>{_ID})\.map\('
    rf'\((?P<tool>{_ID})\)=>(?P<serialize>{_ID})\((?P=tool),\{{'
    rf'getToolPermissionContext:(?P<context>{_ID})\.getToolPermissionContext,'
    rf'tools:(?P<all_tools>{_ID}),agents:(?P=context)\.agents,'
    rf'allowedAgentTypes:(?P=context)\.allowedAgentTypes,model:(?P<model>{_ID}),'
    rf'deferLoading:(?P<deferred>{_ID})\((?P=tool)\)\}}\)\)\);'
)
_MULTI_PROVIDER_CONTEXT_WINDOW = re.compile(
    rf'(?P<prefix>function (?P<function>{_ID})\((?P<model>{_ID}),(?P<headers>{_ID})\)'
    rf'\{{let (?P<override>{_ID})=(?P<override_fn>{_ID})\(\);'
    rf'if\((?P=override)!==void 0\)return (?P=override);)'
    rf'(?P<native>if\((?P<extended>{_ID})\((?P=model),(?P=headers)\)\)'
    rf'return (?P<extended_window>{_ID});return (?P<nested>{_ID})'
    rf'\((?P=model),(?P=headers)\)\}})'
)
_MULTI_PROVIDER_MAX_OUTPUT = re.compile(
    rf'(?P<prefix>function (?P<function>{_ID})\((?P<model>{_ID})\)\{{let '
    rf'(?P<default>{_ID}),(?P<upper>{_ID}),(?P<normalized>{_ID})='
    rf'(?P<normalize>{_ID})\((?P=model)\);.{{0,2000}}?let (?P<config>{_ID})='
    rf'(?P<config_fn>{_ID})\((?P=model)\);if\((?P=config)\?\.max_tokens&&'
    rf'(?P=config)\.max_tokens>=4096\)(?P=upper)=(?P=config)\.max_tokens,'
    rf'(?P=default)=Math\.min\((?P=default),(?P=upper)\);)'
    rf'(?P<return>return\{{default:(?P=default),upperLimit:(?P=upper)\}}\}})'
)
_MULTI_PROVIDER_ATTRIBUTION = re.compile(
    rf"let (?P<model>{_ID})=(?P<current>{_ID})\(\),(?P<label>{_ID})="
    rf'(?P<native_label>[^;]{{1,300}}),(?P<pr>{_ID})=`\\uD83E\\uDD16 Generated with '
    rf'\[Claude Code\]\(\$\{{(?P<url>{_ID})\}}\)`,(?P<commit>{_ID})=`Co-Authored-By: '
    rf'\$\{{(?P=label)\}} <noreply@anthropic\.com>`,(?P<settings>{_ID})=(?P<load>{_ID})\(\);'
)


_MULTI_PROVIDER_COMPACTION_SOURCE = re.compile(
    rf'function (?P<function>{_ID})\((?P<model>{_ID}),(?P<setting>{_ID})\)\{{let\{{'
    rf'source:(?P<source>{_ID})\}}=(?P<resolver>{_ID})\((?P=model),(?P=setting)\);'
    rf'return (?P=source)==="env"\|\|(?P=source)==="settings"\|\|'
    rf'(?P=source)==="model-default"\}}'
)


def _replace_multi_provider_sdk_tail(match: re.Match[str]) -> str:
    return (
        match.group("prefix")
        + f"const _ccMultiProviderSDK=()=>{match.group('constructor')};"
        + _MULTI_PROVIDER_HELPER
        + match.group("next")
    )


def _replace_multi_provider_thinking_filter(match: re.Match[str]) -> str:
    messages = match.group("messages")
    model = match.group("model")
    message = match.group("message")
    return (
        f"function {match.group('function')}({messages},{model}){{let _ccProvider="
        f"_ccMultiProviderModelProvider({model});return {match.group('filter')}("
        f"{messages},({message})=>{message}.message.model!=="
        f"{match.group('synthetic')}&&_ccMultiProviderModelProvider("
        f"{message}.message.model)!==_ccProvider)}}"
    )


def _route_multi_provider_request(match: re.Match[str]) -> str:
    client = match.group("client")
    request = match.group("request")
    options = match.group("options")
    return (
        f"let _ccRequest={request},_ccOptions={options},"
        f"[_ccClient,_ccOutbound,_ccOutboundOptions]=_ccMultiProviderRoute({client},"
        f"_ccRequest,_ccOptions);let {match.group('response')}=await "
        "_ccClient.beta.messages.create(_ccOutbound,_ccOutboundOptions"
    )


def _route_multi_provider_side_query(match: re.Match[str]) -> str:
    options = match.group("options")
    return (
        f"let {match.group('started')}=performance.now(),_ccRequest="
        f"{match.group('request')},_ccOptions={options},"
        f"[_ccClient,_ccOutbound,_ccOutboundOptions]=_ccMultiProviderRoute("
        f"{match.group('client')},_ccRequest,_ccOptions),{match.group('response')}="
        "await _ccClient.beta.messages.create(_ccOutbound,_ccOutboundOptions"
    )


def _route_multi_provider_count_tokens(match: re.Match[str]) -> str:
    request = match.group("request").replace(
        f"KA({match.group('model')})", "KA(_ccEffectiveModel)", 1
    )
    return (
        "_ccMultiProviderPreflight(_ccEffectiveModel);let "
        f"{match.group('client')}=await LF({{maxRetries:1,model:"
        "_ccEffectiveModel,source:\"count_tokens\"}),"
        f"{match.group('betas')}={match.group('raw_betas')}.filter("
        f"({match.group('beta')})=>ij6.has({match.group('beta')})),_ccRequest="
        f"{request},"
        "[_ccClient,_ccOutbound]="
        f"_ccMultiProviderRoute({match.group('client')},_ccRequest),"
        f"{match.group('response')}=await _ccClient.beta.messages.countTokens("
        "_ccOutbound)"
    )


def _surface_multi_provider_count_tokens_error(match: re.Match[str]) -> str:
    error = match.group("error")
    prefix = match.group("prefix")
    model_arg = match.group("model_arg")
    model_binding = re.search(
        rf"let (?P<effective>{_ID})={re.escape(model_arg)}\?\?(?P<default>{_ID})\(\)",
        prefix,
    )
    if model_binding is None:
        raise PatchError(
            "multi-provider-sdk: countTokens effective model binding absent"
        )
    effective = model_binding.group("effective")
    callback_try = "async()=>{try{"
    if callback_try not in prefix:
        raise PatchError("multi-provider-sdk: countTokens try scope changed")
    prefix = prefix.replace(
        callback_try, "async()=>{let _ccEffectiveModel;try{", 1
    ).replace(
        model_binding.group(0),
        f"_ccEffectiveModel={model_arg}??{model_binding.group('default')}()",
        1,
    )
    prefix = re.sub(rf"\b{re.escape(effective)}\b", "_ccEffectiveModel", prefix)
    response = match.group("response")
    return_suffix = f"return {response}.input_tokens"
    if not prefix.endswith(return_suffix):
        raise PatchError("multi-provider-sdk: countTokens return shape changed")
    prefix = prefix[: -len(return_suffix)]
    return (
        prefix
        + f"return _ccMultiProviderInputTokens(_ccEffectiveModel,{response})}}"
        + f'catch({error}){{if(_ccMultiProviderModelInfo(_ccEffectiveModel))throw {error};'
        + match.group("body")
        + "}})}"
    )


def _add_multi_provider_picker_models(match: re.Match[str]) -> str:
    return (
        match.group(0)
        + f"{match.group('options')}.push(..._ccMultiProviderPickerCatalog());"
    )


def _recognize_multi_provider_model(match: re.Match[str]) -> str:
    model = match.group("model")
    name = match.group("name")
    return (
        f"function {match.group('function')}({model}){{let _ccKnown="
        f"_ccMultiProviderCatalog.find((_ccModel)=>_ccModel.value==={model});"
        f"if(_ccKnown)return{{..._ccKnown}};let {name}={match.group('display')}({model});"
        f"if(!{name})return null;let {match.group('normalized')}="
        f"{match.group('normalize')}({model}),{match.group('alias')}=null;"
        f'if({match.group("normalized")}.includes("fable"))'
    )


def _filter_multi_provider_tool_schemas(match: re.Match[str]) -> str:
    tools = match.group("tools")
    tool = match.group("tool")
    return match.group(0).replace(
        f"{tools}.map(({tool})=>",
        f"{tools}.filter(({tool})=>_ccMultiProviderToolAllowed("
        f"{match.group('model')},{tool})).map(({tool})=>",
        1,
    )


def _resolve_multi_provider_context_window(match: re.Match[str]) -> str:
    model = match.group("model")
    return (
        match.group("prefix")
        + f"let _ccProviderModel=_ccMultiProviderCatalogInfo({model});"
        + "if(_ccProviderModel)return _ccProviderModel.contextWindow;"
        + match.group("native")
    )


def _resolve_multi_provider_max_output(match: re.Match[str]) -> str:
    model = match.group("model")
    default = match.group("default")
    upper = match.group("upper")
    return (
        match.group("prefix")
        + f"let _ccProviderModel=_ccMultiProviderCatalogInfo({model});"
        + f"if(_ccProviderModel){upper}=_ccProviderModel.maxOutputTokens,"
        + f"{default}=Math.min({default},{upper});"
        + match.group("return")
    )


def _replace_multi_provider_attribution(match: re.Match[str]) -> str:
    model = match.group("model")
    label = match.group("label")
    pr = match.group("pr")
    commit = match.group("commit")
    settings = match.group("settings")
    return (
        f"let {model}={match.group('current')}(),"
        f"_ccNativeAttributionLabel={match.group('native_label')},"
        f"{{label:{label},domain:_ccAttributionDomain}}="
        f"_ccMultiProviderAttribution({model},_ccNativeAttributionLabel),"
        f"{pr}=`\\uD83E\\uDD16 Generated with [Claude Code](${{{match.group('url')}}})`,"
        f"{commit}=`Co-Authored-By: ${{{label}}} <noreply@${{_ccAttributionDomain}}>`,"
        f"{settings}={match.group('load')}();"
    )


def _mark_multi_provider_compaction_source(match: re.Match[str]) -> str:
    source = match.group("source")
    model = match.group("model")
    return (
        match.group(0)[:-1]
        + f'||({source}==="auto"&&_ccMultiProviderCatalogInfo({model})!==null)'
        + "}"
    )


# The router propagates provider compatibility errors. It does not fall back to
# Anthropic or change the native client's retry policy.
MULTI_PROVIDER_SDK = PatchSet(
    name="multi-provider-sdk",
    patches=(
        _model_costs_patch(_MULTI_PROVIDER_MODEL_COSTS),
        Patch(
            "capture-generic-anthropic-sdk",
            _MULTI_PROVIDER_SDK_TAIL,
            _replace_multi_provider_sdk_tail,
        ),
        Patch(
            "resolve-provider-context-window",
            _MULTI_PROVIDER_CONTEXT_WINDOW,
            _resolve_multi_provider_context_window,
        ),
        Patch(
            "resolve-provider-max-output",
            _MULTI_PROVIDER_MAX_OUTPUT,
            _resolve_multi_provider_max_output,
        ),
        Patch(
            "select-provider-attribution",
            _MULTI_PROVIDER_ATTRIBUTION,
            _replace_multi_provider_attribution,
        ),
        Patch(
            "mark-provider-compaction-source",
            _MULTI_PROVIDER_COMPACTION_SOURCE,
            _mark_multi_provider_compaction_source,
        ),
        Patch(
            "retain-same-provider-thinking",
            _MULTI_PROVIDER_THINKING_FILTER,
            _replace_multi_provider_thinking_filter,
        ),
        Patch(
            "route-nonstreaming-fallback",
            _MULTI_PROVIDER_NONSTREAMING,
            _route_multi_provider_request,
        ),
        Patch(
            "route-main-streaming",
            _MULTI_PROVIDER_STREAMING,
            _route_multi_provider_request,
        ),
        Patch(
            "route-side-query",
            _MULTI_PROVIDER_SIDE_QUERY,
            _route_multi_provider_side_query,
        ),
        Patch(
            "route-count-tokens",
            _MULTI_PROVIDER_COUNT_TOKENS,
            _route_multi_provider_count_tokens,
        ),
        Patch(
            "surface-count-tokens-provider-errors",
            _MULTI_PROVIDER_COUNT_TOKENS_CATCH,
            _surface_multi_provider_count_tokens_error,
        ),
        Patch(
            "add-model-picker-entries",
            _MULTI_PROVIDER_PICKER,
            _add_multi_provider_picker_models,
        ),
        Patch(
            "restore-provider-qualified-session-model",
            _MULTI_PROVIDER_RESUME,
            _restore_multi_provider_model,
        ),
        Patch(
            "expand-agent-model-catalogue",
            _MULTI_PROVIDER_AGENT_MODEL,
            _expand_multi_provider_agent_model,
        ),
        Patch(
            "recognize-qualified-models",
            _MULTI_PROVIDER_RECOGNITION,
            _recognize_multi_provider_model,
        ),
        Patch(
            "allow-web-search-only-for-anthropic-models",
            _MULTI_PROVIDER_TOOL_SCHEMA,
            _filter_multi_provider_tool_schemas,
        ),
    ),
    verify_present=(
        re.compile(r"const _ccMultiProviderSDK=\(\)=>[\w$]+"),
        re.compile(r"function _ccMultiProviderRoute\("),
        re.compile(r'maxRetries:0'),
        re.compile(r'code:"EPROVIDERCREDENTIAL"'),
        re.compile(r'_ccMultiProviderDeniedRequestFields'),
        re.compile(r'beta\.messages\.countTokens\(_ccOutbound\)'),
        re.compile(r"https://api\.kimi\.com/coding"),
        re.compile(r"https://api\.z\.ai/api/anthropic"),
        re.compile(r"https://api\.minimax\.io/anthropic"),
        re.compile(r"KimiCLI/1\.5"),
        re.compile(r"function _ccMultiProviderCatalogInfo\("),
        re.compile(r'if\(_ccCandidates\.length===1\)'),
        re.compile(r'model:[\w$]+\.enum\(\[\.\.\.new Set\('),
        re.compile(
            r"\{label:[\w$]+,domain:_ccAttributionDomain\}="
            r"_ccMultiProviderAttribution\([\w$]+,_ccNativeAttributionLabel\)"
        ),
        re.compile(
            r"<noreply@\$\{_ccAttributionDomain\}>",
        ),
        re.compile(r"_ccProviderModel\.contextWindow"),
        re.compile(r"_ccProviderModel\.maxOutputTokens"),
        re.compile(r'==="auto"&&_ccMultiProviderCatalogInfo\('),
        re.compile(r"_ccMultiProviderPickerCatalog\(\)"),
        re.compile(r'CC_OPENAI_AVAILABLE'),
        re.compile(r'CC_OPENAI_PROXY_AUTH_TOKEN'),
        re.compile(r'"kimi:kimi-k3":\{inputTokens:3,outputTokens:15'),
        re.compile(r'"zai:glm-5\.3-flash":\{inputTokens:0\.15,outputTokens:0\.5'),
        re.compile(r'"minimax:minimax-m3":\{inputTokens:0\.3,outputTokens:1\.2'),
        re.compile(r'function _ccMultiProviderToolAllowed\('),
        re.compile(
            r'\.filter\(\([\w$]+\)=>_ccMultiProviderToolAllowed\('
            r'[\w$]+,[\w$]+\)\)\.map\('
        ),
    ),
    min_version=_V_2_1_174,
    max_version=(2, 1, 175),
    requires_version=True,
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
        # the generic tool-use renderer calls H.tool.renderToolUseMessage(...)
        # UNCONDITIONALLY (no ?.), and it is not an aK/base default -- omitting it
        # throws `undefined(...)` on any transcript render of the tool. null == no
        # custom render line, matching TodoWrite's renderToolUseMessage(){return null}.
        "renderToolUseMessage(){return null},"
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


def default_patch_sets(version: Version | None) -> list[PatchSet]:
    """The patch sets applied by ``ccpatch apply`` (order matters)."""
    return [
        thinking_expanded(version),
        CHANNELS_ENABLED,
        DEV_CHANNEL_INHERITANCE,
        BACKGROUND_PROVIDER_ENV,
        MULTI_PROVIDER_SDK,
        CATPPUCCIN_SYNTAX,
        THINKING_SUMMARIES_NONINTERACTIVE,
        COMPACT_SESSION,
    ]
