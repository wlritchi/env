"""rg wrapper that strips grep-style `-r` misuse inside Claude Code.

Agents habitually run `rg -rn pattern` as if rg were grep. In rg, `-r` is
--replace and consumes a value, so `-rn` silently replaces every match with
the literal text `n`. When CLAUDECODE=1, this shim strips `-r` uses that look
like grep habit and rejects ones that look like a deliberate replacement,
directing the caller to the unambiguous long form `--replace`. `--replace`
itself always passes through untouched. Outside Claude Code the shim execs rg
unchanged.

Installed as the `rg-shim` console script and wired up via `alias rg=rg-shim`
in env.bash.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
from typing import NoReturn

# Short flags that take no value (rg 15).
ZERO_ARG_SHORTS = frozenset("absFivxUPSwL.uhnN0opqHIclVz")

# Short flags that consume a value, attached or as the next argument.
VALUE_SHORTS = frozenset("efEmjgdtTABCMr")

# Long flags that consume the next argument when not written as --flag=value.
VALUE_LONGS = frozenset(
    {
        "--after-context",
        "--before-context",
        "--color",
        "--colors",
        "--context",
        "--context-separator",
        "--dfa-size-limit",
        "--encoding",
        "--engine",
        "--field-context-separator",
        "--field-match-separator",
        "--file",
        "--generate",
        "--glob",
        "--hostname-bin",
        "--hyperlink-format",
        "--iglob",
        "--ignore-file",
        "--max-columns",
        "--max-count",
        "--max-depth",
        "--max-filesize",
        "--path-separator",
        "--pre",
        "--pre-glob",
        "--regex-size-limit",
        "--regexp",
        "--replace",
        "--sort",
        "--sortr",
        "--threads",
        "--type",
        "--type-add",
        "--type-clear",
        "--type-not",
    }
)

DELIBERATE_MSG = """\
rg-shim: this `-r` looks like a deliberate rg replacement, but `-r` is usually
grep habit (rg is recursive by default and has no recursion flag; rg's -r is
--replace). If you really want a replacement, use the unambiguous long form
--replace=REPLACEMENT; otherwise omit -r."""

# `$` followed by a capture reference (`$1`, `${name}`, `$name`) or the `$$`
# literal-dollar escape. A bare trailing `$` is more likely a regex anchor in
# a pattern that -r accidentally swallowed, so it does not count.
_REPLACEMENT_REF = re.compile(r"\$(?:\{|\w|\$)")


class DeliberateReplaceError(Exception):
    """Raised when a `-r` use looks like an intentional rg-style replacement."""


def _looks_like_replacement(value: str) -> bool:
    return bool(_REPLACEMENT_REF.search(value))


def _strip_r_from_cluster(
    cluster: str, r_index: int, next_arg: str | None
) -> str | None:
    """Handle `r` found at r_index in a short-flag cluster (leading `-` removed).

    Everything before r_index is known to be zero-arg flags. Returns the
    rewritten argument (or None to drop it entirely) with `-r` stripped, or
    raises DeliberateReplaceError.
    """
    prefix = cluster[:r_index]
    rest = cluster[r_index + 1 :]
    if not rest:
        # `-r` at the end of a cluster: its value would be the next argument.
        if next_arg is not None and _looks_like_replacement(next_arg):
            raise DeliberateReplaceError(DELIBERATE_MSG)
        return f"-{prefix}" if prefix else None
    if _looks_like_replacement(rest):
        raise DeliberateReplaceError(DELIBERATE_MSG)
    if all(ch in ZERO_ARG_SHORTS for ch in rest):
        # e.g. `-rn`, `-rin`: reads as a grep-style flag cluster.
        return f"-{prefix}{rest}"
    # The attached value isn't a plausible flag cluster, so it was probably
    # meant as a replacement (or is too ambiguous to strip silently).
    raise DeliberateReplaceError(DELIBERATE_MSG)


def transform_args(args: list[str]) -> list[str]:
    """Return args with grep-style `-r` stripped; raise on deliberate-looking use."""
    out: list[str] = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--":
            out.extend(args[i:])
            break
        if arg.startswith("--"):
            out.append(arg)
            i += 1
            if "=" not in arg and arg in VALUE_LONGS and i < len(args):
                out.append(args[i])
                i += 1
            continue
        if arg.startswith("-") and len(arg) > 1:
            cluster = arg[1:]
            for idx, ch in enumerate(cluster):
                if ch == "r":
                    next_arg = args[i + 1] if i + 1 < len(args) else None
                    replacement_arg = _strip_r_from_cluster(cluster, idx, next_arg)
                    if replacement_arg is not None:
                        out.append(replacement_arg)
                    i += 1
                    break
                if ch in VALUE_SHORTS:
                    out.append(arg)
                    i += 1
                    if idx == len(cluster) - 1 and i < len(args):
                        # Value is the next argument; don't scan it for `r`.
                        out.append(args[i])
                        i += 1
                    break
                if ch not in ZERO_ARG_SHORTS:
                    # Unknown flag; pass through and let rg complain.
                    out.append(arg)
                    i += 1
                    break
            else:
                out.append(arg)
                i += 1
            continue
        out.append(arg)
        i += 1
    return out


def main() -> NoReturn:
    rg = shutil.which("rg")
    if rg is None:
        print("rg-shim: rg not found on PATH", file=sys.stderr)
        sys.exit(127)
    args = sys.argv[1:]
    if os.environ.get("CLAUDECODE") == "1":
        try:
            args = transform_args(args)
        except DeliberateReplaceError as e:
            print(e, file=sys.stderr)
            sys.exit(2)
    os.execv(rg, [rg, *args])


if __name__ == "__main__":
    main()
