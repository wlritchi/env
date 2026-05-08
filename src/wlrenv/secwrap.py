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
from dataclasses import dataclass, field
from pathlib import Path

_ENV_LINE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


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
        elif a == "--" or not a.startswith("-"):
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
        if explicit is not None:
            if explicit not in _BACKENDS:
                raise BackendError(
                    f"SECWRAP_BACKEND={explicit!r} is not one of pass, passage"
                )
            store = cls._resolve_store(explicit)
            if store is None:
                raise BackendError(
                    f"SECWRAP_BACKEND={explicit!r} but no store directory found"
                )
            binary, ext, _ = _BACKENDS[explicit]
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


def main(argv: list[str] | None = None) -> int:
    del argv  # unused in stub
    print("secwrap (python stub)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
