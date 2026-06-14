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


def default_patch_sets(version: Version | None) -> list[PatchSet]:
    """The patch sets applied by ``ccpatch apply`` (order matters)."""
    return [thinking_expanded(version)]
