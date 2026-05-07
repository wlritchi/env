# `secwrap` Phase 1 (Python rewrite) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the bash `secwrap` with a Python implementation in `src/wlrenv/secwrap.py`, distributed via the `wlrenv` package's `[project.scripts]` entry. Functional parity with the current bash (`--help`, `--list`, `--from`, KEY=VALUE wrap, exec) plus runtime backend detection.

**Architecture:** Single Python file (`src/wlrenv/secwrap.py`) with a `Backend` dataclass abstracting pass/passage, pure functions for argv and KEY=VALUE parsing, and a `main()` that wires them together. Backend selection: read `$SECWRAP_BACKEND` if set; otherwise auto-detect by checking for `passage` binary + `$PASSAGE_DIR`/default store, falling back to `pass` + `$PASSWORD_STORE_DIR`/default store. The Nix derivation `machines/pkgs/secwrap.nix` is removed; secwrap travels with the wlrenv package via `uv tool install`.

**Tech Stack:** Python 3.12, pytest, pytest-mock (added to dev deps), ruff, pyright.

**Scope (out of scope):** Includes, `_SECWRAP_LOADED` marker, meta key, bootstrap/rotate/doctor subcommands. All deferred to Phases 2–3 — this plan stops at functional parity with the bash version, plus the runtime backend detection from the design.

**Spec reference:** `docs/specs/2026-05-07-secwrap-includes-design.md` (especially "Implementation Language" → "Backend selection", and "Implementation Sequencing" → Phase 1).

---

## File structure

| Path                                | Action  | Responsibility                                                            |
|-------------------------------------|---------|---------------------------------------------------------------------------|
| `src/wlrenv/secwrap.py`             | Create  | Single-file implementation: argv parsing, backend abstraction, `main()`.  |
| `tests/test_secwrap.py`             | Create  | Pytest unit tests for all pure functions and `main()` via `mocker`.       |
| `pyproject.toml`                    | Modify  | Add `secwrap = 'wlrenv.secwrap:main'` to `[project.scripts]`. Add `pytest-mock` to `[dependency-groups].dev`. |
| `machines/pkgs/secwrap.nix`         | Delete  | No longer the delivery vehicle.                                           |
| `machines/linux.nix`                | Modify  | Remove the `secwrap = import …` let-binding and drop it from `home.packages`. |
| `machines/darwin.nix`               | Modify  | Same as linux.nix.                                                        |

The Nix derivation deletion is the **last** task so the bash `secwrap` keeps working until the Python version is verified. PATH precedence is `~/.nix-profile/bin` ahead of `~/.local/bin`, so during development the Nix-installed bash version stays on PATH; tests invoke the Python module directly via `pytest`.

---

## Tasks

### Task 1: Add `pytest-mock` to dev deps and pin a `tests/conftest.py`

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add pytest-mock to the `dev` dependency group**

In `pyproject.toml`, locate the `[dependency-groups]` block and add `"pytest-mock>=3.14.0,<4.0.0"` to the `dev` list (alphabetical order with the existing entries — between `pytest` and `ruff`).

Resulting block:

```toml
[dependency-groups]
dev = [
    "codespell>=2.4.1",
    "prek>=0.2.25",
    "pyright>=1.1.407,<2.0.0",
    "pytest>=9.0.1,<10.0.0",
    "pytest-mock>=3.14.0,<4.0.0",
    "ruff>=0.9.6,<0.10.0",
    "ty>=0.0.7",
    "types-tqdm",
    "yamllint>=1.35.1,<2.0.0",
]
```

- [ ] **Step 2: Sync the lockfile**

Run: `cd /home/wlritchi/.wlrenv && uv lock`
Expected: `uv.lock` updated; exit 0.

- [ ] **Step 3: Verify `pytest-mock` is importable**

Run: `cd /home/wlritchi/.wlrenv && uv run --group dev python -c "from pytest_mock import MockerFixture; print('ok')"`
Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore(deps): add pytest-mock for upcoming secwrap tests"
```

---

### Task 2: Add `secwrap` console script entry and stub module

**Files:**
- Create: `src/wlrenv/secwrap.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Create the stub module**

Create `src/wlrenv/secwrap.py`:

```python
"""secwrap: wrap commands with secrets from pass/passage.

This module is the Python rewrite of the bash secwrap script. Phase 1 covers
functional parity with the original (plus runtime backend detection); Phase 2
adds includes and the loaded marker; Phase 3 adds the gpg meta-key flow.

See docs/specs/2026-05-07-secwrap-includes-design.md.
"""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    del argv  # unused in stub
    print("secwrap (python stub)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Register the entry point**

In `pyproject.toml`, locate `[project.scripts]` and add `secwrap = 'wlrenv.secwrap:main'` in alphabetical order (it goes before `wlr-niri-librewolf-host`):

```toml
[project.scripts]
retainer = 'wlrenv.retainer:cli_main'
secwrap = 'wlrenv.secwrap:main'
wlr-niri-librewolf-host = 'wlrenv.niri.cli:librewolf_native_host_cli'
wlr-niri-restore-mosh = 'wlrenv.niri.cli:restore_mosh_cli'
wlr-niri-restore-tmux = 'wlrenv.niri.cli:restore_tmux_cli'
wlr-niri-track-terminals = 'wlrenv.niri.cli:track_terminals_cli'
xonsh = 'xonsh.main:main'
```

- [ ] **Step 3: Verify the stub runs as a module**

Run: `cd /home/wlritchi/.wlrenv && uv run python -m wlrenv.secwrap`
Expected: stderr `secwrap (python stub)`, exit 0.

- [ ] **Step 4: Run the autoformatter**

Run: `cd /home/wlritchi/.wlrenv && uv tool run ruff format src/wlrenv/secwrap.py`
Expected: `1 file left unchanged` (or `1 file reformatted`); exit 0.

- [ ] **Step 5: Commit**

```bash
git add src/wlrenv/secwrap.py pyproject.toml
git commit -m "feat(secwrap): add Python module stub and console script entry"
```

---

### Task 3: Implement and test `parse_env_lines`

The bash version exports lines that match `^[A-Za-z_][A-Za-z0-9_]*=` after skipping empty/comment lines. Replicate exactly: the value is treated as literal (no quote stripping, no escape processing), KEY=VALUE pairs are returned as a `dict[str, str]` with later occurrences overriding earlier ones.

**Files:**
- Create: `tests/test_secwrap.py`
- Modify: `src/wlrenv/secwrap.py`

- [ ] **Step 1: Write failing tests for `parse_env_lines`**

Create `tests/test_secwrap.py`:

```python
from __future__ import annotations

from wlrenv.secwrap import parse_env_lines


def test_parse_env_lines_empty() -> None:
    assert parse_env_lines("") == {}


def test_parse_env_lines_basic() -> None:
    assert parse_env_lines("FOO=bar\n") == {"FOO": "bar"}


def test_parse_env_lines_multiple() -> None:
    content = "FOO=bar\nBAZ=qux\n"
    assert parse_env_lines(content) == {"FOO": "bar", "BAZ": "qux"}


def test_parse_env_lines_skips_blank_lines() -> None:
    content = "\nFOO=bar\n\nBAZ=qux\n\n"
    assert parse_env_lines(content) == {"FOO": "bar", "BAZ": "qux"}


def test_parse_env_lines_skips_comments() -> None:
    content = "# comment\nFOO=bar\n  # indented comment\nBAZ=qux\n"
    assert parse_env_lines(content) == {"FOO": "bar", "BAZ": "qux"}


def test_parse_env_lines_skips_invalid_keys() -> None:
    # Keys must match [A-Za-z_][A-Za-z0-9_]*
    content = "1FOO=bar\nFO O=bar\n=bar\nFOO=ok\n"
    assert parse_env_lines(content) == {"FOO": "ok"}


def test_parse_env_lines_value_with_equals() -> None:
    # Only the FIRST '=' splits; the rest is literal value.
    content = "TOKEN=abc=def==\n"
    assert parse_env_lines(content) == {"TOKEN": "abc=def=="}


def test_parse_env_lines_value_with_quotes_kept_literal() -> None:
    # Matches bash `export "FOO=\"bar\""`: quotes are literal in the value.
    content = 'FOO="bar"\n'
    assert parse_env_lines(content) == {"FOO": '"bar"'}


def test_parse_env_lines_later_wins() -> None:
    content = "FOO=first\nFOO=second\n"
    assert parse_env_lines(content) == {"FOO": "second"}


def test_parse_env_lines_no_trailing_newline() -> None:
    assert parse_env_lines("FOO=bar") == {"FOO": "bar"}


def test_parse_env_lines_crlf() -> None:
    # Carriage returns are part of the value if present (bash behavior).
    # We don't strip them — keeps parity.
    assert parse_env_lines("FOO=bar\r\n") == {"FOO": "bar\r"}
```

- [ ] **Step 2: Run tests; confirm they fail with ImportError**

Run: `cd /home/wlritchi/.wlrenv && uv run --group dev pytest tests/test_secwrap.py -v`
Expected: `ImportError: cannot import name 'parse_env_lines' from 'wlrenv.secwrap'` or equivalent collection error.

- [ ] **Step 3: Implement `parse_env_lines`**

Add the following to `src/wlrenv/secwrap.py` (above `main`):

```python
import re

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
```

Move the `import re` to the top of the file alongside `import sys` (per CLAUDE.md: imports at top, not local).

- [ ] **Step 4: Run tests; confirm they pass**

Run: `cd /home/wlritchi/.wlrenv && uv run --group dev pytest tests/test_secwrap.py -v`
Expected: 11 passed.

- [ ] **Step 5: Run autoformatter**

Run: `cd /home/wlritchi/.wlrenv && uv tool run ruff format src/wlrenv/secwrap.py tests/test_secwrap.py`
Expected: at most "2 files reformatted"; exit 0.

- [ ] **Step 6: Commit**

```bash
git add src/wlrenv/secwrap.py tests/test_secwrap.py
git commit -m "feat(secwrap): parse_env_lines with bash-parity semantics"
```

---

### Task 4: Implement and test argv parsing

The current bash accepts `--help`, `--list`, `--from <name>`, and bare `--` is implicit (everything after the last shim flag is the command and its args). Flags MUST appear before the command. Replicate that exactly.

**Files:**
- Modify: `src/wlrenv/secwrap.py`
- Modify: `tests/test_secwrap.py`

- [ ] **Step 1: Write failing tests for `parse_args`**

Append to `tests/test_secwrap.py`:

```python
import pytest

from wlrenv.secwrap import Args, ArgError, parse_args


def test_parse_args_help() -> None:
    args = parse_args(["--help"])
    assert args.help_mode is True
    assert args.list_mode is False
    assert args.from_name is None
    assert args.command is None
    assert args.forwarded == []


def test_parse_args_list() -> None:
    args = parse_args(["--list"])
    assert args.list_mode is True
    assert args.command is None


def test_parse_args_simple_wrap() -> None:
    args = parse_args(["aws", "s3", "ls"])
    assert args.help_mode is False
    assert args.list_mode is False
    assert args.from_name is None
    assert args.command == "aws"
    assert args.forwarded == ["s3", "ls"]


def test_parse_args_from() -> None:
    args = parse_args(["--from", "claude", "node", "script.js"])
    assert args.from_name == "claude"
    assert args.command == "node"
    assert args.forwarded == ["script.js"]


def test_parse_args_from_missing_value() -> None:
    with pytest.raises(ArgError, match="--from requires"):
        parse_args(["--from"])


def test_parse_args_unknown_flag() -> None:
    with pytest.raises(ArgError, match="unknown option"):
        parse_args(["--bogus", "tool"])


def test_parse_args_no_command() -> None:
    # No flags, no command -> Args with command=None
    args = parse_args([])
    assert args.command is None


def test_parse_args_flags_after_command_are_forwarded() -> None:
    # --help after the command name is forwarded, not consumed.
    args = parse_args(["aws", "--help"])
    assert args.help_mode is False
    assert args.command == "aws"
    assert args.forwarded == ["--help"]


def test_parse_args_double_dash_separator_treated_as_command() -> None:
    # `--` is not special; if a user passes it as the first non-flag,
    # it becomes the "command" (which will then fail to exec, but parsing
    # itself doesn't reject it). Matches bash behavior.
    args = parse_args(["--", "echo", "hi"])
    assert args.command == "--"
    assert args.forwarded == ["echo", "hi"]


def test_parse_args_negative_token_is_unknown_flag() -> None:
    # Bash rejects unknown -* tokens before the command.
    with pytest.raises(ArgError, match="unknown option"):
        parse_args(["-x", "tool"])
```

- [ ] **Step 2: Run tests; confirm new tests fail**

Run: `cd /home/wlritchi/.wlrenv && uv run --group dev pytest tests/test_secwrap.py -v`
Expected: previous 11 still pass; new 10 fail with `ImportError: cannot import name 'Args'` (or similar).

- [ ] **Step 3: Implement `Args`, `ArgError`, and `parse_args`**

Add to `src/wlrenv/secwrap.py` (above `main`, below `parse_env_lines`):

```python
from dataclasses import dataclass, field


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
        elif a.startswith("-"):
            raise ArgError(f"unknown option: {a}")
        else:
            break

    command = args[0] if args else None
    forwarded = args[1:] if args else []
    return Args(
        help_mode=help_mode,
        list_mode=list_mode,
        from_name=from_name,
        command=command,
        forwarded=forwarded,
    )
```

Move `from dataclasses import dataclass, field` to the top-of-file imports.

- [ ] **Step 4: Run tests; confirm all pass**

Run: `cd /home/wlritchi/.wlrenv && uv run --group dev pytest tests/test_secwrap.py -v`
Expected: 21 passed.

- [ ] **Step 5: Run autoformatter**

Run: `cd /home/wlritchi/.wlrenv && uv tool run ruff format src/wlrenv/secwrap.py tests/test_secwrap.py`

- [ ] **Step 6: Commit**

```bash
git add src/wlrenv/secwrap.py tests/test_secwrap.py
git commit -m "feat(secwrap): argv parsing with --help/--list/--from"
```

---

### Task 5: Implement and test `Backend` (detection + show + list_tools)

The `Backend` dataclass holds the runtime-resolved backend identity (name, binary, extension, store path). Detection rules per spec:

1. If `$SECWRAP_BACKEND` is set, use it (`pass` or `passage`); error on any other value.
2. Else: if `passage` binary on PATH and `$PASSAGE_DIR` (or default `~/.passage/store`) exists, use passage.
3. Else: if `pass` binary on PATH and `$PASSWORD_STORE_DIR` (or default `~/.password-store`) exists, use pass.
4. Else: hard error with stderr message; caller exits non-zero.

`Backend.show(secret_path)` returns the decrypted blob as `str`, or `None` if the entry doesn't exist (subprocess returns non-zero). `Backend.list_tools()` walks `<store>/config/env/` for files matching `*.<ext>` and returns base names without the extension, sorted.

**Files:**
- Modify: `src/wlrenv/secwrap.py`
- Modify: `tests/test_secwrap.py`

- [ ] **Step 1: Write failing tests for `Backend.detect`**

Append to `tests/test_secwrap.py`:

```python
from pathlib import Path
from unittest.mock import MagicMock

from pytest_mock import MockerFixture

from wlrenv.secwrap import Backend, BackendError


def test_backend_detect_env_passage(mocker: MockerFixture, tmp_path: Path) -> None:
    store = tmp_path / "passage-store"
    store.mkdir()
    mocker.patch.dict(
        "os.environ",
        {"SECWRAP_BACKEND": "passage", "PASSAGE_DIR": str(store)},
        clear=True,
    )
    b = Backend.detect()
    assert b.name == "passage"
    assert b.extension == "age"
    assert b.store_dir == store


def test_backend_detect_env_pass(mocker: MockerFixture, tmp_path: Path) -> None:
    store = tmp_path / "pw-store"
    store.mkdir()
    mocker.patch.dict(
        "os.environ",
        {"SECWRAP_BACKEND": "pass", "PASSWORD_STORE_DIR": str(store)},
        clear=True,
    )
    b = Backend.detect()
    assert b.name == "pass"
    assert b.extension == "gpg"
    assert b.store_dir == store


def test_backend_detect_env_unknown_value(mocker: MockerFixture) -> None:
    mocker.patch.dict("os.environ", {"SECWRAP_BACKEND": "weird"}, clear=True)
    with pytest.raises(BackendError, match="SECWRAP_BACKEND"):
        Backend.detect()


def test_backend_detect_autodetect_passage_wins(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    passage_store = tmp_path / "passage-store"
    passage_store.mkdir()
    pass_store = tmp_path / "pw-store"
    pass_store.mkdir()
    mocker.patch.dict(
        "os.environ",
        {"PASSAGE_DIR": str(passage_store), "PASSWORD_STORE_DIR": str(pass_store)},
        clear=True,
    )
    mocker.patch(
        "shutil.which",
        side_effect=lambda name: f"/usr/bin/{name}" if name in {"pass", "passage"} else None,
    )
    b = Backend.detect()
    assert b.name == "passage"


def test_backend_detect_autodetect_pass_only(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    pass_store = tmp_path / "pw-store"
    pass_store.mkdir()
    mocker.patch.dict(
        "os.environ",
        {"PASSWORD_STORE_DIR": str(pass_store)},
        clear=True,
    )
    mocker.patch(
        "shutil.which",
        side_effect=lambda name: "/usr/bin/pass" if name == "pass" else None,
    )
    b = Backend.detect()
    assert b.name == "pass"


def test_backend_detect_autodetect_no_backend(mocker: MockerFixture) -> None:
    mocker.patch.dict("os.environ", {}, clear=True)
    mocker.patch("shutil.which", return_value=None)
    with pytest.raises(BackendError, match="no usable backend"):
        Backend.detect()


def test_backend_detect_autodetect_binary_present_store_missing(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    # passage binary present but its store doesn't exist -> fall through.
    # pass binary present and its store exists -> pass wins.
    pass_store = tmp_path / "pw-store"
    pass_store.mkdir()
    mocker.patch.dict(
        "os.environ",
        {"PASSWORD_STORE_DIR": str(pass_store)},
        clear=True,
    )
    mocker.patch(
        "shutil.which",
        side_effect=lambda name: f"/usr/bin/{name}" if name in {"pass", "passage"} else None,
    )
    b = Backend.detect()
    assert b.name == "pass"


def test_backend_show_returns_content(mocker: MockerFixture, tmp_path: Path) -> None:
    store = tmp_path / "store"
    store.mkdir()
    backend = Backend(name="pass", binary="pass", extension="gpg", store_dir=store)
    completed = MagicMock(spec=["returncode", "stdout", "stderr"])
    completed.returncode = 0
    completed.stdout = "FOO=bar\n"
    completed.stderr = ""
    run_mock = mocker.patch("subprocess.run", return_value=completed)
    result = backend.show("config/env/aws")
    assert result == "FOO=bar\n"
    run_mock.assert_called_once()
    args, kwargs = run_mock.call_args
    assert args[0][:2] == ["pass", "show"]
    assert args[0][2] == "config/env/aws"


def test_backend_show_returns_none_on_missing(mocker: MockerFixture, tmp_path: Path) -> None:
    backend = Backend(name="pass", binary="pass", extension="gpg", store_dir=tmp_path)
    completed = MagicMock(spec=["returncode", "stdout", "stderr"])
    completed.returncode = 1
    completed.stdout = ""
    completed.stderr = "Error: aws is not in the password store."
    mocker.patch("subprocess.run", return_value=completed)
    assert backend.show("config/env/aws") is None


def test_backend_list_tools_empty(tmp_path: Path) -> None:
    backend = Backend(name="passage", binary="passage", extension="age", store_dir=tmp_path)
    assert backend.list_tools() == []


def test_backend_list_tools_lists_entries(tmp_path: Path) -> None:
    env_dir = tmp_path / "config" / "env"
    env_dir.mkdir(parents=True)
    (env_dir / "aws.age").write_bytes(b"")
    (env_dir / "claude.age").write_bytes(b"")
    (env_dir / "ignored.gpg").write_bytes(b"")  # wrong extension
    (env_dir / "subdir").mkdir()  # directories are skipped
    backend = Backend(name="passage", binary="passage", extension="age", store_dir=tmp_path)
    assert backend.list_tools() == ["aws", "claude"]
```

- [ ] **Step 2: Run tests; confirm new tests fail**

Run: `cd /home/wlritchi/.wlrenv && uv run --group dev pytest tests/test_secwrap.py -v`
Expected: previous 21 still pass; the 11 new tests fail with import errors.

- [ ] **Step 3: Implement `Backend` and `BackendError`**

Add to `src/wlrenv/secwrap.py` (above `main`, below `parse_args`):

```python
import os
import shutil
import subprocess
from pathlib import Path

# (Top-of-file imports already include `os`, `shutil`, `subprocess` after this task.)


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
            "no usable backend found; install pass or passage, "
            "or set SECWRAP_BACKEND"
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
```

Move `import os`, `import shutil`, `import subprocess`, and `from pathlib import Path` to the top of the file.

- [ ] **Step 4: Run tests; confirm all pass**

Run: `cd /home/wlritchi/.wlrenv && uv run --group dev pytest tests/test_secwrap.py -v`
Expected: 32 passed.

- [ ] **Step 5: Run autoformatter**

Run: `cd /home/wlritchi/.wlrenv && uv tool run ruff format src/wlrenv/secwrap.py tests/test_secwrap.py`

- [ ] **Step 6: Commit**

```bash
git add src/wlrenv/secwrap.py tests/test_secwrap.py
git commit -m "feat(secwrap): Backend dataclass with detect/show/list_tools"
```

---

### Task 6: Wire up `main()` and write integration-shaped tests

`main()` glues argv parsing, backend detection, and entry loading together, then `os.execvpe`s the wrapped command with merged env. Errors print to stderr and exit non-zero; help/list write to stdout and exit 0.

The bash version's `usage()` text is reproduced verbatim minus the obsolete language about "configured password store" (we now print the detected backend on `--help` for clarity).

**Files:**
- Modify: `src/wlrenv/secwrap.py`
- Modify: `tests/test_secwrap.py`

- [ ] **Step 1: Write failing tests for `main()`**

Append to `tests/test_secwrap.py`:

```python
from wlrenv.secwrap import USAGE


def test_main_help_prints_usage_and_exits_zero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from wlrenv.secwrap import main

    rc = main(["--help"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "secwrap" in captured.out
    assert "Usage:" in captured.out


def test_main_list_prints_tools(
    mocker: MockerFixture, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    env_dir = tmp_path / "config" / "env"
    env_dir.mkdir(parents=True)
    (env_dir / "aws.age").write_bytes(b"")
    (env_dir / "claude.age").write_bytes(b"")
    mocker.patch.dict(
        "os.environ",
        {"SECWRAP_BACKEND": "passage", "PASSAGE_DIR": str(tmp_path)},
        clear=True,
    )
    from wlrenv.secwrap import main

    rc = main(["--list"])
    assert rc == 0
    out_lines = capsys.readouterr().out.strip().splitlines()
    assert out_lines == ["aws", "claude"]


def test_main_no_command_prints_usage_to_stderr_and_exits_one(
    mocker: MockerFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    mocker.patch.dict("os.environ", {"SECWRAP_BACKEND": "pass"}, clear=True)
    # Backend.detect needs a real store dir; skip detection by going straight
    # to the "no command" branch via empty argv.
    from wlrenv.secwrap import main

    rc = main([])
    assert rc == 1
    assert "Usage:" in capsys.readouterr().err


def test_main_unknown_flag_exits_one(
    mocker: MockerFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    from wlrenv.secwrap import main

    rc = main(["--bogus"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "unknown option" in err


def test_main_wrap_with_entry_execs_with_merged_env(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    mocker.patch.dict(
        "os.environ",
        {"SECWRAP_BACKEND": "passage", "PASSAGE_DIR": str(tmp_path), "PATH": "/usr/bin"},
        clear=True,
    )
    completed = MagicMock(spec=["returncode", "stdout", "stderr"])
    completed.returncode = 0
    completed.stdout = "TOKEN=abc\nREGION=us-east-1\n"
    completed.stderr = ""
    mocker.patch("subprocess.run", return_value=completed)
    execvpe = mocker.patch("os.execvpe")

    from wlrenv.secwrap import main

    main(["aws", "s3", "ls"])

    execvpe.assert_called_once()
    file_arg, argv_arg, env_arg = execvpe.call_args.args
    assert file_arg == "aws"
    assert argv_arg == ["aws", "s3", "ls"]
    assert env_arg["TOKEN"] == "abc"
    assert env_arg["REGION"] == "us-east-1"
    assert env_arg["PATH"] == "/usr/bin"  # original env preserved


def test_main_wrap_no_entry_execs_with_unmodified_env(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    mocker.patch.dict(
        "os.environ",
        {"SECWRAP_BACKEND": "passage", "PASSAGE_DIR": str(tmp_path), "PATH": "/usr/bin"},
        clear=True,
    )
    completed = MagicMock(spec=["returncode", "stdout", "stderr"])
    completed.returncode = 1
    completed.stdout = ""
    completed.stderr = "Error: aws is not in the password store."
    mocker.patch("subprocess.run", return_value=completed)
    execvpe = mocker.patch("os.execvpe")

    from wlrenv.secwrap import main

    main(["aws", "--version"])

    execvpe.assert_called_once()
    file_arg, argv_arg, env_arg = execvpe.call_args.args
    assert file_arg == "aws"
    assert argv_arg == ["aws", "--version"]
    assert "TOKEN" not in env_arg
    assert env_arg["PATH"] == "/usr/bin"


def test_main_wrap_uses_from_for_lookup_not_command(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    mocker.patch.dict(
        "os.environ",
        {"SECWRAP_BACKEND": "passage", "PASSAGE_DIR": str(tmp_path)},
        clear=True,
    )
    completed = MagicMock(spec=["returncode", "stdout", "stderr"])
    completed.returncode = 0
    completed.stdout = "TOKEN=abc\n"
    completed.stderr = ""
    run_mock = mocker.patch("subprocess.run", return_value=completed)
    execvpe = mocker.patch("os.execvpe")

    from wlrenv.secwrap import main

    main(["--from", "claude", "node", "script.js"])

    # subprocess.run called with config/env/claude (the --from name), not 'node'.
    pass_args = run_mock.call_args.args[0]
    assert pass_args[2] == "config/env/claude"
    # exec called with the actual command 'node'.
    file_arg, argv_arg, _ = execvpe.call_args.args
    assert file_arg == "node"
    assert argv_arg == ["node", "script.js"]


def test_usage_constant_mentions_options() -> None:
    assert "--from" in USAGE
    assert "--list" in USAGE
    assert "--help" in USAGE
```

- [ ] **Step 2: Run tests; confirm new tests fail**

Run: `cd /home/wlritchi/.wlrenv && uv run --group dev pytest tests/test_secwrap.py -v`
Expected: previous 32 still pass; new 8 fail (importing `USAGE` and broken stub `main`).

- [ ] **Step 3: Replace the stub `main()` and add `USAGE`**

Replace the contents of `src/wlrenv/secwrap.py` (keeping everything you've added so far for `parse_env_lines`, `parse_args`, `Args`, `ArgError`, `Backend`, `BackendError`):

```python
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


class ArgError(ValueError):
    """Raised when argv parsing fails. Caller renders to stderr and exits 1."""


class BackendError(RuntimeError):
    """Raised when the backend cannot be resolved or invoked."""


_ENV_LINE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")

_BACKENDS: dict[str, tuple[str, str, str]] = {
    # name -> (binary, extension, default store dir relative to $HOME)
    "passage": ("passage", "age", ".passage/store"),
    "pass": ("pass", "gpg", ".password-store"),
}

_STORE_ENV: dict[str, str] = {
    "passage": "PASSAGE_DIR",
    "pass": "PASSWORD_STORE_DIR",
}


def parse_env_lines(content: str) -> dict[str, str]:
    """Parse KEY=VALUE lines from a decrypted secret blob.

    Skips blank lines and lines whose first non-whitespace char is `#`. Keys
    must match `[A-Za-z_][A-Za-z0-9_]*`; non-matching lines are silently
    dropped (parity with the bash version).
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
        out[m.group(1)] = m.group(2)
    return out


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
        elif a.startswith("-"):
            raise ArgError(f"unknown option: {a}")
        else:
            break

    command = args[0] if args else None
    forwarded = args[1:] if args else []
    return Args(
        help_mode=help_mode,
        list_mode=list_mode,
        from_name=from_name,
        command=command,
        forwarded=forwarded,
    )


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

        for name in ("passage", "pass"):
            binary, ext, _ = _BACKENDS[name]
            if shutil.which(binary) is None:
                continue
            store = cls._resolve_store(name)
            if store is None:
                continue
            return cls(name=name, binary=binary, extension=ext, store_dir=store)

        raise BackendError(
            "no usable backend found; install pass or passage, "
            "or set SECWRAP_BACKEND"
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

    try:
        backend = Backend.detect()
    except BackendError as exc:
        print(f"secwrap: {exc}", file=sys.stderr)
        return 1

    if args.list_mode:
        for tool in backend.list_tools():
            print(tool)
        return 0

    assert args.command is not None  # for type checker; checked above
    secret_key = args.from_name if args.from_name is not None else args.command
    blob = backend.show(f"config/env/{secret_key}")
    env = os.environ.copy()
    if blob is not None:
        env.update(parse_env_lines(blob))

    os.execvpe(args.command, [args.command, *args.forwarded], env)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests; confirm all pass**

Run: `cd /home/wlritchi/.wlrenv && uv run --group dev pytest tests/test_secwrap.py -v`
Expected: 40 passed.

- [ ] **Step 5: Run autoformatter and pyright**

Run: `cd /home/wlritchi/.wlrenv && uv tool run ruff format src/wlrenv/secwrap.py tests/test_secwrap.py`
Run: `cd /home/wlritchi/.wlrenv && uv run --group dev pyright src/wlrenv/secwrap.py`
Expected: 0 errors.

- [ ] **Step 6: Commit**

```bash
git add src/wlrenv/secwrap.py tests/test_secwrap.py
git commit -m "feat(secwrap): wire up main() with help, list, and wrap flows"
```

---

### Task 7: Smoke-test the installed entry point against the live store

The entry point is registered in `[project.scripts]`, so `uv tool install` will place a `secwrap` binary in `~/.local/share/uv/tools/wlrenv/bin/`. The Nix-installed bash `secwrap` is still on PATH ahead of it (`~/.nix-profile/bin/` precedence), so we test the Python version explicitly via the package path.

**Files:** none modified (verification only).

- [ ] **Step 1: Reinstall the wlrenv tool to pick up the new entry**

Run: `uv tool install --reinstall /home/wlritchi/.wlrenv/ --python 3.12`
Expected: exit 0; output mentions `Installed 7 executables: ..., secwrap, ...`.

- [ ] **Step 2: Verify the Python `secwrap --help` works**

Run: `~/.local/share/uv/tools/wlrenv/bin/secwrap --help`
Expected: prints USAGE text (including `--from`, `--list`, `--help`); exit 0.

- [ ] **Step 3: Verify `secwrap --list` matches the bash version's output**

Run on a machine with a populated store:
```bash
diff \
  <(/home/wlritchi/.nix-profile/bin/secwrap --list | sort) \
  <(~/.local/share/uv/tools/wlrenv/bin/secwrap --list | sort)
```
Expected: empty diff (lists match).

- [ ] **Step 4: Verify a real wrap works end-to-end**

Pick a tool with an entry that has a harmless `KEY=VALUE`. Run:
```bash
~/.local/share/uv/tools/wlrenv/bin/secwrap <tool> env | grep -E '^(SOME_KEY)='
```
Expected: shows the value from the entry. (Optionally compare to the bash version's output for the same wrap.)

- [ ] **Step 5: Verify backend auto-detection**

Run: `SECWRAP_BACKEND='' ~/.local/share/uv/tools/wlrenv/bin/secwrap --list` (empty value should auto-detect)
Run: `~/.local/share/uv/tools/wlrenv/bin/secwrap --list` (no env var should auto-detect the same way)
Expected: both succeed and produce the same list as Step 3.

If any verification step fails, do NOT proceed to Task 8. Diagnose and fix; rerun the relevant tests; then re-attempt verification.

- [ ] **Step 6: Commit (verification log only — nothing to commit). Skip if no changes.**

If you found and fixed bugs above, the fixes already got their own commits via the TDD loop. This step has no commit; it exists as a checkpoint marker.

---

### Task 8: Remove the Nix derivation and wire it out of consumers

Once the Python version is verified working end-to-end, drop the bash version. After this task, `~/.nix-profile/bin/secwrap` no longer exists, and PATH falls through to `~/.local/bin/secwrap` (the uv-tool-installed Python version).

**Files:**
- Delete: `machines/pkgs/secwrap.nix`
- Modify: `machines/linux.nix`
- Modify: `machines/darwin.nix`

- [ ] **Step 1: Remove `secwrap` from `machines/linux.nix`**

In `machines/linux.nix`, delete:
- The `secwrap = import ./pkgs/secwrap.nix { ... };` let-binding (lines 14–17 in current file).
- The `secwrap` entry in the `home.packages` list (line 40 in current file).

After the edit, the `let` block has only `niri-spacer` and `home.packages` ends with `]` (no `++ [ niri-spacer secwrap ]`-style wrapper):

```nix
let
  hostModule = ./hosts + "/${hostname}.nix";
  hostImports = lib.optional (builtins.pathExists hostModule) hostModule;
  niri-spacer = pkgs.callPackage ./pkgs/niri-spacer.nix { };
in
{
  imports = [ ... ];

  custom.krewPlugins = [ ... ];

  home.packages =
    (with pkgs; [
      mold
      rclone
    ])
    ++ [
      niri-spacer
    ];

  ...
}
```

- [ ] **Step 2: Remove `secwrap` from `machines/darwin.nix`**

In `machines/darwin.nix`, delete:
- The `secwrap = import ./pkgs/secwrap.nix { ... };` let-binding (lines 18–21 in current file).
- Replace the `++ [ secwrap ]` clause at the end of `home.packages` with nothing. The trailing `++ [ secwrap ]` should be deleted entirely; if `home.packages` would end with a dangling `++`, remove that too.

The resulting `home.packages` block should be just the inline `with pkgs; [ ... ]` list (no `++ [ ... ]` extension).

- [ ] **Step 3: Delete the Nix derivation file**

Run: `git rm /home/wlritchi/.wlrenv/machines/pkgs/secwrap.nix`
Expected: file removed from the index.

- [ ] **Step 4: Sanity-check the Nix files still evaluate**

Run: `cd /home/wlritchi/.wlrenv && nix flake check --impure --no-build` (or, if that's slow, `nix eval --impure .#homeConfigurations.default.activationPackage --raw 2>&1 | head -20`).
Expected: evaluation succeeds; no reference to `secwrap.nix`.

If evaluation fails because of a stray `secwrap` reference, search for it:
```bash
grep -rn 'secwrap' machines/
```
Fix any remaining reference, then re-run the eval.

- [ ] **Step 5: Run formatters**

Run: `cd /home/wlritchi/.wlrenv && uv tool run nixfmt machines/linux.nix machines/darwin.nix`
Expected: formatter rewrites or leaves files unchanged; exit 0.

- [ ] **Step 6: Apply the change to the live system**

Run: `wlr-nix-rebuild`
Expected: home-manager activates without error; the old `~/.nix-profile/bin/secwrap` is gone.

- [ ] **Step 7: Verify PATH now resolves to the Python `secwrap`**

Run: `which secwrap`
Expected: `/home/wlritchi/.local/bin/secwrap` (or `~/.local/share/uv/tools/wlrenv/bin/secwrap`), NOT `~/.nix-profile/bin/secwrap`.

Run: `secwrap --help`
Expected: prints the new Python USAGE.

Run: `secwrap --list`
Expected: same output as Task 7 Step 3.

- [ ] **Step 8: Commit**

```bash
git add machines/linux.nix machines/darwin.nix
git rm machines/pkgs/secwrap.nix
git commit -m "refactor(secwrap): remove bash Nix derivation, deliver via wlrenv package

Python implementation in src/wlrenv/secwrap.py (entry point in
[project.scripts]) replaces the writeShellScriptBin derivation. PATH
now resolves through ~/.local/bin instead of ~/.nix-profile/bin for
secwrap; functional parity verified via diff against the prior version."
```

---

## Self-review notes

**Spec coverage:**
- Phase 1 = "functional parity with current bash secwrap, plus runtime backend detection." Covered by Tasks 2–6 (parity) and Task 5 (backend detection).
- Removal of `backend` derivation parameter: Task 8.
- Phase 2 (includes/marker/meta) and Phase 3 (gpg flow) are explicitly out of scope. Each will get its own plan.

**Type consistency:** `Args`, `Backend`, `ArgError`, `BackendError`, `parse_env_lines`, `parse_args`, `USAGE`, `main` — names are consistent across tasks.

**Placeholder scan:** none — every step has either concrete code, a concrete command, or a precise edit instruction.
