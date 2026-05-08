"""secwrap: wrap commands with secrets from pass/passage.

This module is the Python rewrite of the bash secwrap script. Phase 1 covers
functional parity with the original (plus runtime backend detection); Phase 2
adds includes and the loaded marker; Phase 3 adds the gpg meta-key flow.

See docs/specs/2026-05-07-secwrap-includes-design.md.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

USAGE = """\
secwrap - wrap commands with secrets from pass/passage

Usage: secwrap [options] <command> [args...]

Looks up "config/env/<command>" in the configured password store. If found,
parses KEY=VALUE lines and exports them before exec'ing the command. If not
found, exec's the command directly.

Options (must appear before <command>):
  --from <name>   Load secrets for <name> instead of <command>
  --list          List tool names that have entries under config/env/
  --help          Show this help message
"""


_ENV_LINE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
_INCLUDE_LINE = re.compile(r"^\s*#\s*secwrap-include:\s*(.+?)\s*$")
_TOOL_NAME = re.compile(r"^[A-Za-z0-9._-]+$")


def parse_includes(content: str) -> list[str]:
    """Extract tool names from `# secwrap-include: ...` directives.

    Returns names in document order; duplicates preserved (resolver dedupes).
    Tokens not matching `[A-Za-z0-9._-]+` are dropped silently.
    """
    out: list[str] = []
    for raw_line in content.split("\n"):
        m = _INCLUDE_LINE.match(raw_line)
        if m is None:
            continue
        for token in m.group(1).split():
            if _TOOL_NAME.match(token):
                out.append(token)
    return out


def parse_marker(value: str) -> set[str]:
    """Parse a `_SECWRAP_LOADED` value into a set of tool names.

    Tokens not matching `[A-Za-z0-9._-]+` are dropped silently. Empty input
    returns the empty set.
    """
    if not value:
        return set()
    return {tok for tok in value.split(":") if _TOOL_NAME.match(tok)}


def format_marker(names: Iterable[str]) -> str:
    """Format a set of tool names as a canonical `_SECWRAP_LOADED` value.

    Output is alphabetized, deduped, and colon-joined.
    """
    return ":".join(sorted(set(names)))


def parse_env_lines(content: str) -> dict[str, str]:
    """Parse KEY=VALUE lines from a decrypted secret blob.

    Skips blank lines and lines whose first non-whitespace char is `#`.
    Keys must match `[A-Za-z_][A-Za-z0-9_]*`; lines failing that match are
    dropped silently (parity with the bash version, which used the same regex
    and discarded non-matching lines without a warning).
    """
    out: dict[str, str] = {}
    for raw_line in content.split("\n"):
        if not raw_line:
            continue
        if raw_line.lstrip().startswith("#"):
            continue
        m = _ENV_LINE.match(raw_line)
        if m is None:
            continue
        key, value = m.group(1), m.group(2)
        out[key] = value
    return out


class ArgError(ValueError):
    """Raised when argv parsing fails. Caller renders to stderr and exits 1."""


@dataclass(frozen=True)
class Args:
    help_mode: bool = False
    list_mode: bool = False
    from_name: str | None = None
    force_wrap: bool = False
    command: str | None = None
    forwarded: list[str] = field(default_factory=list)


def parse_args(argv: list[str]) -> Args:
    """Parse secwrap's argv. Shim flags are consumed from the front of argv
    until the first non-shim token; the rest is the command and its args.
    """
    help_mode = False
    list_mode = False
    from_name: str | None = None
    args = list(argv)

    while args:
        a = args[0]
        if a == "--help":
            help_mode = True
            del args[0]
        elif a == "--list":
            list_mode = True
            del args[0]
        elif a == "--from":
            if len(args) < 2:
                raise ArgError("--from requires an argument")
            from_name = args[1]
            del args[:2]
        elif a == "--":
            del args[0]
            return Args(
                help_mode=help_mode,
                list_mode=list_mode,
                from_name=from_name,
                force_wrap=True,
                command=args[0] if args else None,
                forwarded=args[1:] if args else [],
            )
        elif not a.startswith("-"):
            break
        else:
            raise ArgError(f"unknown option: {a}")

    command = args[0] if args else None
    forwarded = args[1:] if args else []
    return Args(
        help_mode=help_mode,
        list_mode=list_mode,
        from_name=from_name,
        command=command,
        forwarded=forwarded,
    )


class BackendError(RuntimeError):
    """Raised when the backend cannot be resolved or invoked."""


_BACKENDS: dict[str, tuple[str, str, str]] = {
    # name -> (binary, extension, default_store_dir_relative_to_home)
    "passage": ("passage", "age", ".passage/store"),
    "pass": ("pass", "gpg", ".password-store"),
}

_STORE_ENV: dict[str, str] = {
    "passage": "PASSAGE_DIR",
    "pass": "PASSWORD_STORE_DIR",
}


@dataclass(frozen=True)
class Backend:
    name: str
    binary: str
    extension: str
    store_dir: Path

    @classmethod
    def detect(cls) -> Backend:
        explicit = os.environ.get("SECWRAP_BACKEND")
        if explicit:
            if explicit not in _BACKENDS:
                raise BackendError(
                    f"SECWRAP_BACKEND={explicit!r} is not one of passage, pass"
                )
            binary, ext, _ = _BACKENDS[explicit]
            if shutil.which(binary) is None:
                raise BackendError(
                    f"SECWRAP_BACKEND={explicit} but {binary} binary not found on PATH"
                )
            store = cls._resolve_store(explicit)
            if store is None:
                raise BackendError(
                    f"SECWRAP_BACKEND={explicit!r} but no store directory found"
                )
            return cls(name=explicit, binary=binary, extension=ext, store_dir=store)

        # Auto-detect: passage first, then pass.
        for name in ("passage", "pass"):
            binary, ext, _ = _BACKENDS[name]
            if shutil.which(binary) is None:
                continue
            store = cls._resolve_store(name)
            if store is None:
                continue
            return cls(name=name, binary=binary, extension=ext, store_dir=store)

        raise BackendError(
            "no usable backend found; install pass or passage, or set SECWRAP_BACKEND"
        )

    @staticmethod
    def _resolve_store(name: str) -> Path | None:
        env_var = _STORE_ENV[name]
        _, _, default_rel = _BACKENDS[name]
        raw = os.environ.get(env_var)
        path = Path(raw) if raw else Path.home() / default_rel
        return path if path.is_dir() else None

    def show(self, secret_path: str) -> str | None:
        """Return decrypted content, or None if the entry doesn't exist."""
        result = subprocess.run(  # noqa: S603 - trusted binary, controlled args
            [self.binary, "show", secret_path],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return None
        return result.stdout

    def list_tools(self) -> list[str]:
        """List base names of entries under config/env/ matching our extension."""
        env_dir = self.store_dir / "config" / "env"
        if not env_dir.is_dir():
            return []
        suffix = f".{self.extension}"
        names = [
            p.name[: -len(suffix)]
            for p in env_dir.iterdir()
            if p.is_file() and p.name.endswith(suffix)
        ]
        return sorted(names)


class IncludeError(RuntimeError):
    """Raised when include resolution fails (cycle, missing dep, etc.)."""


_PASS_INCLUDES_WARNING = (
    "secwrap: include comments are not yet implemented for the pass backend; ignoring"  # noqa: S105 - not a password; name refers to `pass` backend
)


def resolve_includes(
    backend: Backend, root: str, marker_loaded: set[str]
) -> list[tuple[str, str | None]]:
    """Walk the include graph from `root` and return entries in merge order.

    Returns a list of (name, blob) pairs:
      - Deepest dependency first, root last.
      - Siblings sorted alphabetically.
      - blob is None when the entry was already in `marker_loaded` and skipped.

    The pass backend does NOT walk includes in Phase 2a; it loads only the
    root and emits a one-time stderr warning if the blob contains include
    comments.

    A missing root returns []. A missing non-root include raises IncludeError.
    A cycle raises IncludeError.
    """
    if backend.name == "pass":
        if root in marker_loaded:
            return [(root, None)]
        blob = backend.show(f"config/env/{root}")
        if blob is None:
            return []
        if parse_includes(blob):
            print(_PASS_INCLUDES_WARNING, file=sys.stderr)
        return [(root, blob)]

    # passage: full graph walk.
    result: list[tuple[str, str | None]] = []
    visited: set[str] = set()
    path: list[str] = []

    def visit(name: str, parent: str | None) -> None:
        if name in visited:
            return
        if name in path:
            cycle = " → ".join([*path[path.index(name) :], name])
            raise IncludeError(f"cycle detected: {cycle}")
        path.append(name)
        try:
            if name in marker_loaded:
                visited.add(name)
                result.append((name, None))
                return
            blob = backend.show(f"config/env/{name}")
            if blob is None:
                if parent is None:
                    return  # root missing → caller falls through
                raise IncludeError(
                    f"{parent} includes '{name}' but config/env/{name} not found"
                )
            for dep in sorted(set(parse_includes(blob))):
                visit(dep, parent=name)
            visited.add(name)
            result.append((name, blob))
        finally:
            path.pop()

    visit(root, parent=None)
    return result


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    try:
        args = parse_args(argv)
    except ArgError as exc:
        print(f"secwrap: {exc}", file=sys.stderr)
        print(USAGE, file=sys.stderr)
        return 1

    if args.help_mode:
        print(USAGE)
        return 0

    if args.command is None and not args.list_mode:
        print(USAGE, file=sys.stderr)
        return 1

    # Marker short-circuit: if the secret_key is already loaded, exec
    # immediately without touching the backend.
    if args.command is not None and not args.list_mode:
        secret_key = args.from_name if args.from_name is not None else args.command
        marker_loaded = parse_marker(os.environ.get("_SECWRAP_LOADED", ""))
        if secret_key in marker_loaded:
            os.execvpe(args.command, [args.command, *args.forwarded], os.environ)  # noqa: S606
            return 0  # unreachable; satisfies type checker

    try:
        backend = Backend.detect()
    except BackendError as exc:
        print(f"secwrap: {exc}", file=sys.stderr)
        return 1

    if args.list_mode:
        for tool in backend.list_tools():
            print(tool)
        return 0

    assert args.command is not None  # narrowed above
    secret_key = args.from_name if args.from_name is not None else args.command
    marker_loaded = parse_marker(os.environ.get("_SECWRAP_LOADED", ""))

    try:
        resolved = resolve_includes(backend, secret_key, marker_loaded)
    except IncludeError as exc:
        print(f"secwrap: {exc}", file=sys.stderr)
        return 1

    env = os.environ.copy()
    for _name, blob in resolved:
        if blob is not None:
            env.update(parse_env_lines(blob))

    if resolved:
        new_marker = format_marker(marker_loaded | {name for name, _ in resolved})
        env["_SECWRAP_LOADED"] = new_marker

    os.execvpe(args.command, [args.command, *args.forwarded], env)  # noqa: S606
    return 0  # unreachable; satisfies type checker


if __name__ == "__main__":
    sys.exit(main())
