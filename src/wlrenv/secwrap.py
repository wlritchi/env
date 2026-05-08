"""secwrap: wrap commands with secrets from pass/passage.

This module is the Python rewrite of the bash secwrap script. Phase 1 covers
functional parity with the original (plus runtime backend detection); Phase 2
adds includes and the loaded marker; Phase 3 adds the gpg meta-key flow.

See docs/specs/2026-05-07-secwrap-includes-design.md.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
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


_SUBCOMMANDS = frozenset({"bootstrap", "rotate-meta", "doctor"})


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


class MetaKeyError(RuntimeError):
    """Raised when meta-key load, parse, or decryption fails."""


class ShellOutError(RuntimeError):
    """Raised when a subprocess invoked by a subcommand exits non-zero."""


@dataclass(frozen=True)
class MetaKey:
    """Holds the age private key in process memory for in-process decryption.

    The `key` field is `bytes` (not `str`) so we can `del` the reference and
    overwrite without re-encoding. Python doesn't guarantee zeroing, but we
    avoid leaving live references through `os.execvpe` to children.
    """

    backend: str
    key: bytes

    def decrypt(self, store_dir: Path, entry: str, extension: str) -> str:
        """Decrypt `config/env/{entry}.{extension}` using this meta key.

        For age: `age -d --identity /dev/stdin <path>`. The key is piped on
        stdin so it never hits disk.
        """
        if self.backend != "age":
            raise MetaKeyError(f"MetaKey.decrypt: unsupported backend {self.backend!r}")
        path = store_dir / "config" / "env" / f"{entry}.{extension}"
        result = subprocess.run(  # noqa: S603 - trusted binary, controlled args
            ["age", "-d", "--identity", "/dev/stdin", str(path)],  # noqa: S607
            input=self.key,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise MetaKeyError(
                f"age decryption failed for {entry}: "
                f"{result.stderr.decode('utf-8', errors='replace').strip()}"
            )
        return result.stdout.decode("utf-8")


def load_meta_key(backend: Backend) -> MetaKey | None:
    """Load and parse `config/env-meta`. Returns None if the entry is absent.

    Raises MetaKeyError on JSON parse failure, schema mismatch, or
    backend-mismatch with the runtime-detected backend.
    """
    blob = backend.show("config/env-meta")
    if blob is None:
        return None
    try:
        data = json.loads(blob)
    except json.JSONDecodeError as exc:
        raise MetaKeyError("config/env-meta is not valid JSON") from exc
    if not isinstance(data, dict):
        raise MetaKeyError("config/env-meta is not a JSON object")
    declared = data.get("backend")
    expected = "age" if backend.name == "passage" else "gpg"
    if declared != expected:
        raise MetaKeyError(
            f"config/env-meta declares backend={declared} "
            f"but detected backend is {backend.name}"
        )
    if "key" not in data:
        raise MetaKeyError("config/env-meta missing required field 'key'")
    assert isinstance(declared, str)  # narrowed by the != expected check above
    return MetaKey(backend=declared, key=data["key"].encode("utf-8"))


class IncludeError(RuntimeError):
    """Raised when include resolution fails (cycle, missing dep, etc.)."""


_PASS_INCLUDES_WARNING = (
    "secwrap: include comments are not yet implemented for the pass backend; ignoring"  # noqa: S105 - not a password; name refers to `pass` backend
)


def resolve_includes(
    backend: Backend,
    root: str,
    marker_loaded: set[str],
    meta_key: MetaKey | None = None,
) -> list[tuple[str, str | None]]:
    """Walk the include graph from `root` and return entries in merge order.

    Returns a list of (name, blob) pairs:
      - Deepest dependency first, root last.
      - Siblings sorted alphabetically.
      - blob is None when the entry was already in `marker_loaded` and skipped.

    When `meta_key` is provided, decryption goes through `meta_key.decrypt()`
    (in-process via age) instead of `backend.show()` (subprocess that prompts
    for credentials). The meta key path is only taken on the passage backend;
    the pass backend short-circuits as in Phase 2a.

    The pass backend does NOT walk includes in Phase 2b; it loads only the
    root and emits a one-time stderr warning if the blob contains include
    comments. The pass backend has no meta key in Phase 2b.

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
            if meta_key is not None:
                try:
                    blob = meta_key.decrypt(backend.store_dir, name, backend.extension)
                except MetaKeyError:
                    blob = None
            else:
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


def _add_age_recipient(store_dir: Path, pubkey: str) -> None:
    """Append `pubkey` to `<store_dir>/config/env/.age-recipients` if absent.

    Idempotent. Atomic write via tempfile + os.replace.
    """
    recipients_path = store_dir / "config" / "env" / ".age-recipients"
    existing: list[str] = []
    if recipients_path.exists():
        existing = [
            line for line in recipients_path.read_text().splitlines() if line.strip()
        ]
    if pubkey in existing:
        return
    new_contents = "\n".join([*existing, pubkey]) + "\n"
    recipients_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = recipients_path.parent / (recipients_path.name + ".tmp")
    tmp.write_text(new_contents)
    tmp.replace(recipients_path)


def _run_or_fail(
    cmd: list[str], purpose: str, *, input: str | None = None
) -> subprocess.CompletedProcess[str]:
    """Run `cmd`; raise ShellOutError on non-zero exit."""
    result = subprocess.run(  # noqa: S603 - trusted binary, controlled args
        cmd,
        capture_output=True,
        text=True,
        check=False,
        input=input,
    )
    if result.returncode != 0:
        raise ShellOutError(
            f"{purpose} failed: {result.stderr.strip() or result.stdout.strip()}"
        )
    return result


def do_bootstrap(backend: Backend, args: list[str]) -> int:
    # Pre-flight checks. Check binaries before shelling out for meta-existence.
    if shutil.which("age-keygen") is None:
        print("secwrap: age-keygen not found on PATH", file=sys.stderr)
        return 1
    if shutil.which("passage") is None:
        print("secwrap: passage not found on PATH", file=sys.stderr)
        return 1
    if backend.show("config/env-meta") is not None:
        print(
            "secwrap: config/env-meta already exists; "
            "use `secwrap rotate-meta --yes` to replace it",
            file=sys.stderr,
        )
        return 1

    # Generate key.
    with tempfile.NamedTemporaryFile(
        mode="w", prefix="secwrap-meta-", suffix=".txt", delete=False
    ) as tf:
        keyfile = Path(tf.name)
    try:
        # Try -pq (post-quantum); fall back to default if unsupported.
        result = subprocess.run(  # noqa: S603 - trusted binary, controlled args
            ["age-keygen", "-pq", "-o", str(keyfile)],  # noqa: S607
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            _run_or_fail(
                ["age-keygen", "-o", str(keyfile)],
                "age-keygen",
            )
        # Extract pubkey.
        pubkey_result = _run_or_fail(
            ["age-keygen", "-y", str(keyfile)],
            "age-keygen -y",
        )
        pubkey = pubkey_result.stdout.strip()

        # Add to recipients.
        _add_age_recipient(backend.store_dir, pubkey)

        # Re-encrypt the env subtree.
        _run_or_fail(
            ["passage", "reencrypt", "config/env"],
            "passage reencrypt",
        )

        # Insert meta entry.
        key_content = keyfile.read_text().strip()
        payload = json.dumps({"backend": "age", "key": key_content})
        _run_or_fail(
            ["passage", "insert", "-m", "config/env-meta"],
            "passage insert config/env-meta",
            input=payload + "\n",
        )

        print("secwrap: bootstrap complete", file=sys.stderr)
        return 0
    except ShellOutError as exc:
        print(f"secwrap: {exc}", file=sys.stderr)
        return 1
    finally:
        # Best-effort secure deletion.
        if shutil.which("shred") is not None:
            subprocess.run(  # noqa: S603 - trusted binary, controlled args
                ["shred", "-u", str(keyfile)],  # noqa: S607
                capture_output=True,
                check=False,
            )
        if keyfile.exists():
            keyfile.unlink()


def _swap_age_recipient(store_dir: Path, old_pubkey: str, new_pubkey: str) -> None:
    """Atomically replace `old_pubkey` with `new_pubkey` in `.age-recipients`.

    Removes any line equal to `old_pubkey`. Adds `new_pubkey` if not already present.
    Single atomic write via tempfile + os.replace.
    """
    recipients_path = store_dir / "config" / "env" / ".age-recipients"
    existing: list[str] = []
    if recipients_path.exists():
        existing = [
            line for line in recipients_path.read_text().splitlines() if line.strip()
        ]
    filtered = [line for line in existing if line != old_pubkey]
    if new_pubkey not in filtered:
        filtered.append(new_pubkey)
    if filtered == existing:
        return
    new_contents = "\n".join(filtered) + ("\n" if filtered else "")
    recipients_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = recipients_path.parent / (recipients_path.name + ".tmp")
    tmp.write_text(new_contents)
    tmp.replace(recipients_path)


def do_rotate_meta(backend: Backend, args: list[str]) -> int:
    if "--yes" not in args:
        print(
            "rotate-meta will:\n"
            "  1. Generate a new age meta key.\n"
            "  2. Replace the old recipient in .age-recipients with the new pubkey.\n"
            "  3. Run `passage reencrypt config/env`.\n"
            "  4. Replace config/env-meta with the new key.\n"
            "\n"
            "Re-run with --yes to proceed.",
        )
        return 0

    # Load and parse the existing meta entry.
    existing_blob = backend.show("config/env-meta")
    if existing_blob is None:
        print(
            "secwrap: no config/env-meta found; run `secwrap bootstrap` first",
            file=sys.stderr,
        )
        return 1
    try:
        existing = json.loads(existing_blob)
        if not isinstance(existing, dict):
            raise ValueError("expected JSON object")
        old_key = existing["key"]
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        print(f"secwrap: existing config/env-meta is malformed: {exc}", file=sys.stderr)
        return 1

    # Pre-flight binary checks.
    if shutil.which("age-keygen") is None:
        print("secwrap: age-keygen not found on PATH", file=sys.stderr)
        return 1
    if shutil.which("passage") is None:
        print("secwrap: passage not found on PATH", file=sys.stderr)
        return 1

    # Derive old pubkey to know which recipient to remove.
    with tempfile.NamedTemporaryFile(
        mode="w", prefix="secwrap-old-meta-", suffix=".txt", delete=False
    ) as tf:
        old_keyfile = Path(tf.name)
        tf.write(old_key)
    new_keyfile: Path | None = None
    try:
        old_pubkey_result = _run_or_fail(
            ["age-keygen", "-y", str(old_keyfile)],
            "age-keygen -y (old key)",
        )
        old_pubkey = old_pubkey_result.stdout.strip()

        # Generate new key.
        with tempfile.NamedTemporaryFile(
            mode="w", prefix="secwrap-new-meta-", suffix=".txt", delete=False
        ) as tf:
            new_keyfile = Path(tf.name)
        result = subprocess.run(  # noqa: S603 - trusted binary, controlled args
            ["age-keygen", "-pq", "-o", str(new_keyfile)],  # noqa: S607
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            _run_or_fail(
                ["age-keygen", "-o", str(new_keyfile)],
                "age-keygen (new key)",
            )

        new_pubkey_result = _run_or_fail(
            ["age-keygen", "-y", str(new_keyfile)],
            "age-keygen -y (new key)",
        )
        new_pubkey = new_pubkey_result.stdout.strip()

        # Update recipients atomically: remove old, add new in a single write.
        _swap_age_recipient(backend.store_dir, old_pubkey, new_pubkey)

        # Re-encrypt the env subtree.
        _run_or_fail(
            ["passage", "reencrypt", "config/env"],
            "passage reencrypt",
        )

        # Replace meta entry.
        new_key_content = new_keyfile.read_text().strip()
        payload = json.dumps({"backend": "age", "key": new_key_content})
        _run_or_fail(
            ["passage", "insert", "--force", "-m", "config/env-meta"],
            "passage insert config/env-meta",
            input=payload + "\n",
        )

        print("secwrap: rotate-meta complete", file=sys.stderr)
        return 0
    except ShellOutError as exc:
        print(f"secwrap: {exc}", file=sys.stderr)
        return 1
    finally:
        for path in (old_keyfile, new_keyfile):
            if path is None:
                continue
            if shutil.which("shred") is not None:
                subprocess.run(  # noqa: S603 - trusted binary, controlled args
                    ["shred", "-u", str(path)],  # noqa: S607
                    capture_output=True,
                    check=False,
                )
            if path.exists():
                path.unlink()


def do_doctor(backend: Backend, args: list[str]) -> int:
    raise NotImplementedError("doctor: implemented in Task 7")


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

    assert args.command is not None  # narrowed above

    # Subcommand dispatch (skipped if `--` was used).
    if not args.force_wrap and args.command in _SUBCOMMANDS:
        if backend.name != "passage":
            print(
                f"secwrap: {args.command} is not yet supported for the "
                f"pass backend (will arrive in Phase 3)",
                file=sys.stderr,
            )
            return 1
        if args.command == "bootstrap":
            return do_bootstrap(backend, args.forwarded)
        if args.command == "rotate-meta":
            return do_rotate_meta(backend, args.forwarded)
        if args.command == "doctor":
            return do_doctor(backend, args.forwarded)

    secret_key = args.from_name if args.from_name is not None else args.command
    marker_loaded = parse_marker(os.environ.get("_SECWRAP_LOADED", ""))

    # Marker short-circuit: if the secret_key is already loaded, exec
    # immediately without touching backend.show / decrypt.
    if secret_key in marker_loaded:
        os.execvpe(args.command, [args.command, *args.forwarded], os.environ)  # noqa: S606
        return 0  # unreachable; satisfies type checker

    try:
        meta_key = load_meta_key(backend)
    except MetaKeyError as exc:
        print(f"secwrap: {exc}", file=sys.stderr)
        return 1

    meta_was_absent = meta_key is None

    try:
        resolved = resolve_includes(
            backend, secret_key, marker_loaded, meta_key=meta_key
        )
    except IncludeError as exc:
        print(f"secwrap: {exc}", file=sys.stderr)
        return 1
    finally:
        meta_key = None  # release reference

    decrypt_count = sum(1 for _name, blob in resolved if blob is not None)
    if meta_was_absent and decrypt_count >= 2:
        print(
            f"secwrap: meta key absent; {decrypt_count} includes will require "
            f"{decrypt_count} prompts (run `secwrap bootstrap` to fix)",
            file=sys.stderr,
        )

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
