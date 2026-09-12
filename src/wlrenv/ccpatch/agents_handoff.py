"""Blessed configuration forwarding for the left-arrow agents view."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .patches import PatchSet, Version

_ID = r"[\w$]+"
_HANDOFF = re.compile(
    rf'(?P<gate>{_ID}\("tengu_bg_leftarrow_inprocess",!0\)\)try\{{return await '
    rf'(?P<mount>{_ID})\((?P<job>{_ID}),(?P<load>{_ID}))'
    rf'(?P<options>,\{{dispatchDefaults:(?P<defaults>{_ID})\}})?'
    rf'(?P<middle>\)\}}catch\((?P<error>{_ID})\)\{{{_ID}\((?P=error)\)\}}return '
    rf'{_ID}\(\{{args:\["agents"(?:,\.\.\.{_ID}\((?P=defaults)\))?)'
    rf'(?P<end>\],env:\{{CLAUDE_AGENTS_SELECT:(?P=job),\.\.\.{_ID}\(\)\}}\}}\))'
)
_GETTER = re.compile(
    rf'function (?P<getter>{_ID})\(\)\{{return {_ID}\.replConfigArgv\}}'
)
_PARSER = re.compile(
    rf'function (?P<parser>{_ID})\({_ID}\)\{{let {_ID}=!1,{_ID},'
    rf'{_ID}=\{{addDir:\[\],pluginDir:\[\],'
)
_SERIALIZER = re.compile(
    rf'function (?P<serializer>{_ID})\((?P<config>{_ID})\)'
    rf'\{{return\[\.\.\.(?P=config)\.settings\?\["--settings",'
)
_LEGACY_MOUNT = re.compile(
    rf'(?P<prefix>async function {_ID}\({_ID},{_ID})(?P<body>\)\{{'
    rf'let {_ID}={_ID}\(\)\.catch\(\(\)=>\[\]\);'
    rf'.{{0,1100}}?"\[PERF:bg-leftarrow-mounted\]"\),await {_ID}\({_ID})'
    rf'(?P<end>\),await {_ID}\(0,"other",\{{suppressResumeHint:!0\}}\),'
    rf'process.exit\(0\)\}})'
)


def _helper(match: re.Match[str]) -> str:
    # Import here to avoid a cycle with the patch catalog.
    from .patches import discover_identifiers

    captures = discover_identifiers(match.string, (_PARSER, _SERIALIZER))
    # Use normalized REPL paths and the native configuration allowlist.
    # Preserve approved channels. Exclude all other session and security flags.
    return match.group(0) + (
        'function _ccAgentsDispatchArgs(){let _ccSaved='
        f'{match.group("getter")}()??[],_ccArgs={captures["serializer"]}('
        f'{captures["parser"]}(_ccSaved).config);'
        'return _ccArgs}'
    )


def _handoff(match: re.Match[str]) -> str:
    options = match.group("options")
    if options:
        options = options[:-1] + ',dispatchExtraArgs:_ccAgentsDispatchArgs()}'
    else:
        options = ',{dispatchExtraArgs:_ccAgentsDispatchArgs()}'
    return (
        match.group("gate")
        + options
        + match.group("middle")
        + ',..._ccAgentsDispatchArgs()'
        + match.group("end")
    )


def agents_view_handoff(version: Version | None) -> PatchSet:
    # Import here to avoid a cycle with the patch catalog.
    from .patches import Patch, PatchSet

    legacy = version is not None and version < (2, 1, 195)
    return PatchSet(
        name="agents-view-handoff",
        patches=(
            Patch(
                "agents-channels-serialized-once",
                re.compile(
                    rf',\.\.\.\({_ID}\.channels\?\?\[\]\)\.flatMap\('
                    rf'\((?P<entry>{_ID})\)=>\["--channels",(?P=entry)\]\)'
                ),
                "",
                required=version is None or version >= (2, 1, 187),
            ),
            Patch("agents-config-helper", _GETTER, _helper),
            Patch("agents-launch-config", _HANDOFF, _handoff),
            *(
                (
                    Patch(
                        "agents-legacy-mount-config",
                        _LEGACY_MOUNT,
                        lambda m: (
                            m.group("prefix")
                            + ',_ccOptions'
                            + m.group("body")
                            + ',_ccOptions'
                            + m.group("end")
                        ),
                    ),
                )
                if legacy
                else ()
            ),
        ),
        verify_present=(re.compile(r'dispatchExtraArgs:_ccAgentsDispatchArgs\(\)'),),
        min_version=(2, 1, 182),
        max_version=(2, 1, 207),
        requires_version=True,
    )
