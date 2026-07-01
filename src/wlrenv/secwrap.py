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
import signal
import subprocess
import sys
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from types import FrameType

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


def _gpg_temp_home_root() -> str | None:
    """Pick the best root for a temp GNUPGHOME: XDG_RUNTIME_DIR, TMPDIR, /tmp.

    Prefer `$XDG_RUNTIME_DIR` (tmpfs, per-user, mode 0700 on Linux/systemd),
    then `$TMPDIR` (macOS per-user `/var/folders/...`), then `/tmp`. Returns the
    first writable directory, or None if none is usable.
    """
    candidates = [
        os.environ.get("XDG_RUNTIME_DIR"),
        os.environ.get("TMPDIR"),
        "/tmp",  # noqa: S108 - last-resort fallback; homedir is chmod 700
    ]
    for root in candidates:
        if root and os.path.isdir(root) and os.access(root, os.W_OK):
            return root
    return None


@dataclass(frozen=True)
class MetaKey:
    """Holds the meta private key in process memory for in-process decryption.

    The `key` field is `bytes` (not `str`) so we can `del` the reference and
    overwrite without re-encoding. Python doesn't guarantee zeroing, but we
    avoid leaving live references through `os.execvpe` to children.

    Backends:
      - age (passage): `key` is the age identity, piped to `age -d` per entry;
        stateless, `passphrase` unused, `cleanup()` a no-op.
      - gpg (pass): `key` is an armored, passphrase-protected secret key and
        `passphrase` its passphrase. On first `decrypt()` the key is imported
        into a throwaway `$GNUPGHOME` (tmpfs, mode 0700) that is reused for the
        remaining entries and torn down by `cleanup()`. `_gpg` caches the
        homedir path; mutating this dict is allowed on a frozen instance
        (frozen blocks attribute rebinding, not object mutation).
    """

    backend: str
    key: bytes
    passphrase: bytes | None = None
    _gpg: dict[str, Path] = field(
        default_factory=dict, compare=False, repr=False, hash=False
    )

    def decrypt(self, store_dir: Path, entry: str, extension: str) -> str:
        """Decrypt `config/env/{entry}.{extension}` using this meta key."""
        path = store_dir / "config" / "env" / f"{entry}.{extension}"
        if self.backend == "age":
            return self._decrypt_age(path, entry)
        if self.backend == "gpg":
            return self._decrypt_gpg(path, entry)
        raise MetaKeyError(f"MetaKey.decrypt: unsupported backend {self.backend!r}")

    def _decrypt_age(self, path: Path, entry: str) -> str:
        """`age -d --identity /dev/stdin <path>`; key piped on stdin, never on disk."""
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

    def _ensure_gpg_home(self) -> Path:
        """Lazily create a temp `$GNUPGHOME` and import the meta secret key.

        Importing a passphrase-protected secret key needs no passphrase (the
        passphrase is only required to *use* the key), so the import runs
        without one. The homedir is cached and reused for later entries.
        """
        cached = self._gpg.get("home")
        if cached is not None:
            return cached
        root = _gpg_temp_home_root()
        if root is None:
            raise MetaKeyError(
                "cannot create temp GNUPGHOME (no writable runtime/temp dir found)"
            )
        home = Path(tempfile.mkdtemp(prefix="secwrap-gpg-", dir=root))
        home.chmod(0o700)
        self._gpg["home"] = home
        result = subprocess.run(  # noqa: S603 - trusted binary, controlled args
            ["gpg", "--homedir", str(home), "--batch", "--quiet", "--import"],  # noqa: S607
            input=self.key,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise MetaKeyError(
                "gpg meta key import failed: "
                f"{result.stderr.decode('utf-8', errors='replace').strip()}"
            )
        return home

    def _decrypt_gpg(self, path: Path, entry: str) -> str:
        """Decrypt via gpg in the temp homedir, passphrase piped on stdin (fd 0).

        `--passphrase-fd 0` keeps the passphrase off argv; the ciphertext is
        supplied as a file argument so stdin is free for the passphrase.
        """
        home = self._ensure_gpg_home()
        result = subprocess.run(  # noqa: S603 - trusted binary, controlled args
            [  # noqa: S607 - gpg resolved from PATH
                "gpg",
                "--homedir",
                str(home),
                "--batch",
                "--quiet",
                "--pinentry-mode",
                "loopback",
                "--passphrase-fd",
                "0",
                "--decrypt",
                str(path),
            ],
            input=self.passphrase,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise MetaKeyError(
                f"gpg decryption failed for {entry}: "
                f"{result.stderr.decode('utf-8', errors='replace').strip()}"
            )
        return result.stdout.decode("utf-8")

    def cleanup(self) -> None:
        """Tear down the gpg temp homedir (kill its agent, then remove it).

        Idempotent and safe for the age backend (a no-op when no homedir was
        created). A temp `$GNUPGHOME` spawns its own gpg-agent that would keep
        the tmpfs dir open, so kill it first (best-effort).
        """
        home = self._gpg.pop("home", None)
        if home is None:
            return
        subprocess.run(  # noqa: S603 - trusted binary, controlled args
            ["gpgconf", "--homedir", str(home), "--kill", "gpg-agent"],  # noqa: S607
            capture_output=True,
            check=False,
        )
        shutil.rmtree(home, ignore_errors=True)


def load_meta_key(backend: Backend) -> MetaKey | None:
    """Load and parse `config/env-meta`. Returns None if the entry is absent.

    Raises MetaKeyError on JSON parse failure, schema mismatch, or
    backend-mismatch with the runtime-detected backend. The gpg schema also
    requires a `passphrase` field alongside `key`.
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
    passphrase: bytes | None = None
    if declared == "gpg":
        if "passphrase" not in data:
            raise MetaKeyError("config/env-meta missing required field 'passphrase'")
        passphrase = data["passphrase"].encode("utf-8")
    return MetaKey(
        backend=declared, key=data["key"].encode("utf-8"), passphrase=passphrase
    )


class IncludeError(RuntimeError):
    """Raised when include resolution fails (cycle, missing dep, etc.)."""


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
    (in-process via age or a temp-GNUPGHOME gpg) instead of `backend.show()`
    (subprocess that prompts for credentials). The walk itself is
    backend-agnostic: both passage and pass share this graph traversal, and
    without a meta key each entry is decrypted by `backend.show()`
    (passage/pass show), which the local agent caches.

    A missing root returns []. A missing non-root include raises IncludeError.
    A cycle raises IncludeError.
    """
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


def _classify_pubkey(pubkey: str) -> str:
    """Return 'pq' or 'classic' for an age recipient pubkey.

    age (≥ 1.3.0) refuses to encrypt to recipient sets that mix post-quantum
    and classic keys. PQ recipients use HRP `age1pq` (start with `age1pq1`);
    classic X25519 and plugin recipients (`age1yubikey1...`, `age1se1...`,
    etc.) all sit on the classic side of age's mixing rule.
    """
    return "pq" if pubkey.startswith("age1pq1") else "classic"


def _classify_recipients(recipients: Iterable[str]) -> set[str]:
    """Return the set of distinct recipient kinds present in `recipients`."""
    return {_classify_pubkey(r) for r in recipients if r}


def _generate_meta_key(*, want_pq: bool) -> tuple[str, str]:
    """Generate an age meta key, returning `(key_text, pubkey)`.

    When `want_pq` is True, invokes `age-keygen -pq` and propagates any
    failure (older age binaries that lack `-pq` cannot be reconciled with a
    PQ recipient set, so falling back to classic would just produce a key
    age refuses to mix). When False, invokes plain `age-keygen`.
    """
    if want_pq:
        result = _run_or_fail(
            ["age-keygen", "-pq"],
            "age-keygen -pq (need PQ meta to match existing PQ recipients)",
        )
    else:
        result = _run_or_fail(["age-keygen"], "age-keygen")
    key_text = result.stdout
    pubkey = _run_or_fail(
        ["age-keygen", "-y", "/dev/stdin"],
        "age-keygen -y",
        input=key_text,
    ).stdout.strip()
    return key_text, pubkey


def _derive_recipients_from_identities(identities_path: Path) -> list[str]:
    """Extract pubkeys from a passage identities file.

    Plain `AGE-SECRET-KEY-1...` lines are converted via `age-keygen -y`.
    Plugin identities (e.g. yubikey) carry their pubkey in a `# public key:
    age1...` comment, which `age -e -i <file>` reads directly; we parse those
    comments here so the resulting list matches what passage would derive.
    """
    if not identities_path.is_file():
        return []
    recipients: list[str] = []
    seen: set[str] = set()
    content = identities_path.read_text()

    for line in content.splitlines():
        stripped = line.strip()
        if not stripped.startswith("#"):
            continue
        lower = stripped.lower()
        idx = lower.find("public key:")
        if idx < 0:
            continue
        value = stripped[idx + len("public key:") :].strip()
        if value.startswith("age1") and value not in seen:
            seen.add(value)
            recipients.append(value)

    plain_keys = "\n".join(
        line
        for line in content.splitlines()
        if line.strip().startswith("AGE-SECRET-KEY")
    )
    if plain_keys and shutil.which("age-keygen") is not None:
        result = subprocess.run(  # noqa: S603 - trusted binary, controlled args
            ["age-keygen", "-y", "/dev/stdin"],  # noqa: S607
            input=plain_keys,
            capture_output=True,
            text=True,
            check=False,
        )
        for line in result.stdout.splitlines():
            stripped = line.strip()
            if stripped.startswith("age1") and stripped not in seen:
                seen.add(stripped)
                recipients.append(stripped)

    return recipients


def _resolve_inherited_recipients(store_dir: Path) -> list[str]:
    """Mirror passage's `set_age_recipients()` for the `config/env/` subtree.

    Returns the recipient pubkeys passage would use to encrypt files under
    `<store>/config/env/` if `<store>/config/env/.age-recipients` did NOT
    exist. The local file is deliberately excluded so callers can compare
    "what should be there" against "what is there".

    Resolution order matches FiloSottile/passage's `src/password-store.sh`:
      1. `$PASSAGE_RECIPIENTS_FILE` (file contents).
      2. `$PASSAGE_RECIPIENTS` (whitespace-separated).
      3. Walk up from `config/env/` looking for `.age-recipients` at
         `<store>/config/.age-recipients`, then `<store>/.age-recipients`.
      4. Otherwise derive from `$PASSAGE_IDENTITIES_FILE`
         (default `~/.passage/identities`).
    """
    recipients_file_env = os.environ.get("PASSAGE_RECIPIENTS_FILE")
    if recipients_file_env:
        path = Path(recipients_file_env)
        if path.is_file():
            return [line for line in path.read_text().splitlines() if line.strip()]
        return []

    recipients_env = os.environ.get("PASSAGE_RECIPIENTS")
    if recipients_env:
        return [r for r in recipients_env.split() if r]

    for path in (store_dir / "config", store_dir):
        candidate = path / ".age-recipients"
        if candidate.is_file():
            return [line for line in candidate.read_text().splitlines() if line.strip()]

    identities_env = os.environ.get("PASSAGE_IDENTITIES_FILE")
    identities_path = (
        Path(identities_env)
        if identities_env
        else Path.home() / ".passage" / "identities"
    )
    return _derive_recipients_from_identities(identities_path)


def _write_age_recipients(store_dir: Path, recipients: Iterable[str]) -> None:
    """Atomically write `<store>/config/env/.age-recipients` from `recipients`.

    Deduplicates while preserving order. No-op if the file already has these
    contents (compare-before-write). Atomic via tempfile + os.replace.
    """
    recipients_path = store_dir / "config" / "env" / ".age-recipients"
    seen: set[str] = set()
    deduped: list[str] = []
    for r in recipients:
        if r and r not in seen:
            seen.add(r)
            deduped.append(r)
    new_contents = "\n".join(deduped) + ("\n" if deduped else "")
    if recipients_path.exists() and recipients_path.read_text() == new_contents:
        return
    recipients_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = recipients_path.parent / (recipients_path.name + ".tmp")
    tmp.write_text(new_contents)
    tmp.replace(recipients_path)


def _age_encrypt_to_recipients(
    plaintext: str, recipients: list[str], output_path: Path
) -> None:
    """Encrypt `plaintext` to `output_path` for `recipients` via `age -e`.

    Atomic: writes to a sibling `.tmp` file and renames on success. Raises
    `ShellOutError` if `age` exits non-zero.
    """
    cmd: list[str] = ["age", "-e"]
    for r in recipients:
        cmd.extend(["-r", r])
    tmp_path = output_path.parent / (output_path.name + ".tmp")
    cmd.extend(["-o", str(tmp_path)])
    result = subprocess.run(  # noqa: S603 - trusted binary, controlled args
        cmd,
        input=plaintext.encode("utf-8"),
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass
        raise ShellOutError(
            f"age encrypt failed: "
            f"{result.stderr.decode('utf-8', errors='replace').strip()}"
        )
    tmp_path.replace(output_path)


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

    # Compute the recipient set passage is currently using under config/env/,
    # so the new local .age-recipients we write doesn't drop the user's pubkey.
    # If a local file already exists, use its contents as the base; otherwise
    # resolve the inherited set (walked-up .age-recipients, or identities).
    local_recipients_path = backend.store_dir / "config" / "env" / ".age-recipients"
    if local_recipients_path.is_file():
        base_recipients = [
            line
            for line in local_recipients_path.read_text().splitlines()
            if line.strip()
        ]
    else:
        base_recipients = _resolve_inherited_recipients(backend.store_dir)
    if not base_recipients:
        print(
            "secwrap: cannot determine existing recipients for config/env/ "
            "(no .age-recipients found in ancestors and no pubkeys derived "
            "from $PASSAGE_IDENTITIES_FILE). Set up your identities file or "
            "create config/env/.age-recipients manually before bootstrapping.",
            file=sys.stderr,
        )
        return 1

    # age refuses to encrypt to a mixed PQ/classic recipient set. Pick the
    # meta key type to match the existing recipients; bail if the existing
    # set is itself mixed (age would already be unable to encrypt to it).
    kinds = _classify_recipients(base_recipients)
    if len(kinds) > 1:
        print(
            "secwrap: existing recipients mix post-quantum and classic age keys; "
            "age cannot encrypt to mixed sets. Resolve this in your identities "
            "or .age-recipients file before bootstrapping.",
            file=sys.stderr,
        )
        return 1
    want_pq = kinds == {"pq"}

    try:
        # Capture key on stdout to avoid writing it to disk.
        key_text, pubkey = _generate_meta_key(want_pq=want_pq)

        # Write the local recipients file with the base set + the meta pubkey.
        # Includes the user pubkey(s) so they retain decryption access after
        # `passage reencrypt`.
        _write_age_recipients(backend.store_dir, [*base_recipients, pubkey])

        # Re-encrypt the env subtree.
        _run_or_fail(
            ["passage", "reencrypt", "-p", "config/env"],
            "passage reencrypt",
        )

        # Insert meta entry.
        payload = json.dumps({"backend": "age", "key": key_text.strip()})
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

    try:
        # Derive old pubkey to know which recipient to remove. Pipe old key via stdin.
        old_pubkey_result = _run_or_fail(
            ["age-keygen", "-y", "/dev/stdin"],
            "age-keygen -y (old key)",
            input=old_key,
        )
        old_pubkey = old_pubkey_result.stdout.strip()

        # Match the new meta key type to the existing non-meta recipients,
        # since age refuses mixed sets. Bail on mixed or empty residue.
        recipients_path = backend.store_dir / "config" / "env" / ".age-recipients"
        existing_recipients = (
            [
                line.strip()
                for line in recipients_path.read_text().splitlines()
                if line.strip()
            ]
            if recipients_path.is_file()
            else []
        )
        residue = [r for r in existing_recipients if r != old_pubkey]
        if not residue:
            print(
                "secwrap: .age-recipients has no non-meta recipients to match; "
                "add your identity's pubkey before rotating (otherwise the new "
                "meta key would lock you out, just like the original bootstrap bug).",
                file=sys.stderr,
            )
            return 1
        kinds = _classify_recipients(residue)
        if len(kinds) > 1:
            print(
                "secwrap: .age-recipients mixes post-quantum and classic age keys; "
                "age cannot encrypt to mixed sets. Resolve this before rotating.",
                file=sys.stderr,
            )
            return 1
        want_pq = kinds == {"pq"}

        new_key_text, new_pubkey = _generate_meta_key(want_pq=want_pq)

        # Update recipients atomically: remove old, add new in a single write.
        _swap_age_recipient(backend.store_dir, old_pubkey, new_pubkey)

        # Re-encrypt the env subtree.
        _run_or_fail(
            ["passage", "reencrypt", "-p", "config/env"],
            "passage reencrypt",
        )

        # Replace meta entry.
        payload = json.dumps({"backend": "age", "key": new_key_text.strip()})
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


def _repair_recipients(
    backend: Backend, meta_key: MetaKey, new_recipients: list[str]
) -> int:
    """Repair a broken-bootstrap keystore: rewrite `.age-recipients` to
    `new_recipients` and re-encrypt every `config/env/*.<ext>` entry so the
    new recipient set is honored.

    Returns the number of entries re-encrypted. Raises `MetaKeyError` if
    decryption via the meta key fails, or `ShellOutError` if `age` fails.

    Assumes `meta_key` can decrypt every entry — i.e., the broken state is
    "encrypted only to the meta key", which is exactly what a buggy bootstrap
    produces. If some entries were never re-encrypted to the meta key, this
    helper will fail loudly on the first such entry.
    """
    _write_age_recipients(backend.store_dir, new_recipients)
    count = 0
    for tool in backend.list_tools():
        plaintext = meta_key.decrypt(backend.store_dir, tool, backend.extension)
        target = backend.store_dir / "config" / "env" / f"{tool}.{backend.extension}"
        _age_encrypt_to_recipients(plaintext, new_recipients, target)
        count += 1
    return count


def _repair_with_meta_rotation(
    backend: Backend,
    old_meta_key: MetaKey,
    target_recipients_excluding_meta: list[str],
    *,
    want_pq: bool,
) -> tuple[int, str]:
    """Repair a keystore whose meta key type is incompatible with the
    inherited recipients: decrypt every entry with `old_meta_key`, generate
    a fresh meta key of the requested type, rewrite `.age-recipients` to
    `[*target_recipients_excluding_meta, new_meta_pubkey]`, re-encrypt every
    entry to that set, and finally replace `config/env-meta`.

    Returns `(count_reencrypted, new_meta_pubkey)`.

    Order matters: the old meta entry stays in place until after all
    re-encrypts succeed, so a mid-loop failure leaves untouched entries
    still decryptable by the old meta. Re-encrypt writes are atomic per
    entry via tempfile + rename.
    """
    plaintexts: dict[str, str] = {}
    for tool in backend.list_tools():
        plaintexts[tool] = old_meta_key.decrypt(
            backend.store_dir, tool, backend.extension
        )

    new_key_text, new_pubkey = _generate_meta_key(want_pq=want_pq)
    full_recipients = [*target_recipients_excluding_meta, new_pubkey]
    _write_age_recipients(backend.store_dir, full_recipients)

    for tool, plaintext in plaintexts.items():
        target = backend.store_dir / "config" / "env" / f"{tool}.{backend.extension}"
        _age_encrypt_to_recipients(plaintext, full_recipients, target)

    payload = json.dumps({"backend": "age", "key": new_key_text.strip()})
    _run_or_fail(
        ["passage", "insert", "--force", "-m", "config/env-meta"],
        "passage insert config/env-meta",
        input=payload + "\n",
    )

    return len(plaintexts), new_pubkey


def do_doctor(backend: Backend, args: list[str]) -> int:
    """Verify the meta-key invariants and the include graph.

    Output: progress and per-check status to stdout; failure details to stderr.
    Exit 0 if all clean; 1 if any check fails.

    Passing `--fix` enables repair for missing inherited recipients: doctor
    adds them to `config/env/.age-recipients` and re-encrypts every entry via
    the meta key so the inherited identities can decrypt again.
    """
    fix_mode = "--fix" in args
    failures: list[str] = []
    repairs: list[str] = []

    # Check 1: meta entry exists and parses.
    print("Checking config/env-meta ...", file=sys.stdout)
    try:
        meta_key = load_meta_key(backend)
    except MetaKeyError as exc:
        failures.append(f"meta entry: {exc}")
        meta_key = None
    if meta_key is None and not failures:
        failures.append("config/env-meta missing (run `secwrap bootstrap` first)")
    if not failures:
        print("  OK", file=sys.stdout)

    if meta_key is None:
        print("  Skipping recipient/decrypt/include checks (no meta key).")

    # Check 2: recipients contain meta pubkey AND any inherited recipients
    # (e.g. user identity) that passage would have used pre-bootstrap. A
    # bootstrap before this check was added wrote the local file with only the
    # meta pubkey, locking the user's main identity out of config/env/*.
    if meta_key is not None and shutil.which("age-keygen") is not None:
        print("Checking .age-recipients ...", file=sys.stdout)
        result = subprocess.run(  # noqa: S603 - trusted binary, controlled args
            ["age-keygen", "-y", "/dev/stdin"],  # noqa: S607
            capture_output=True,
            text=True,
            check=False,
            input=meta_key.key.decode("utf-8"),
        )
        if result.returncode != 0:
            failures.append(f"age-keygen -y failed: {result.stderr.strip()}")
        else:
            meta_pubkey = result.stdout.strip()
            recipients_path = backend.store_dir / "config" / "env" / ".age-recipients"
            if not recipients_path.exists():
                failures.append(".age-recipients missing")
            else:
                local_recipients = [
                    line.strip()
                    for line in recipients_path.read_text().splitlines()
                    if line.strip()
                ]
                issues: list[str] = []
                if meta_pubkey not in local_recipients:
                    issues.append(f"meta pubkey {meta_pubkey} not in .age-recipients")
                inherited = _resolve_inherited_recipients(backend.store_dir)
                missing_inherited = [r for r in inherited if r not in local_recipients]
                if missing_inherited:
                    msg = (
                        f".age-recipients missing inherited recipient(s) "
                        f"{', '.join(missing_inherited)}"
                    )
                    if not fix_mode:
                        msg += " (run `secwrap doctor --fix` to repair)"
                    issues.append(msg)

                if fix_mode and missing_inherited:
                    # If the meta key type and the missing inherited recipients
                    # disagree (PQ vs classic), age will refuse to encrypt to
                    # the merged set. Rotate the meta to the inherited's type
                    # as part of the repair so the resulting recipient set is
                    # homogeneous.
                    meta_kind = _classify_pubkey(meta_pubkey)
                    inherited_kinds = _classify_recipients(missing_inherited)
                    try:
                        if len(inherited_kinds) > 1:
                            raise ShellOutError(
                                "missing inherited recipients mix post-quantum "
                                "and classic age keys; resolve this in your "
                                "identities/recipients before --fix"
                            )
                        inherited_kind = next(iter(inherited_kinds))
                        if meta_kind != inherited_kind:
                            # Keep any existing non-meta recipients (e.g. an
                            # already-listed user pubkey of the inherited
                            # kind) and drop the now-stale meta pubkey.
                            target_excl_meta = [
                                r for r in local_recipients if r != meta_pubkey
                            ] + missing_inherited
                            count, new_meta_pubkey = _repair_with_meta_rotation(
                                backend,
                                meta_key,
                                target_excl_meta,
                                want_pq=(inherited_kind == "pq"),
                            )
                            repairs.append(
                                f"rotated meta key ({meta_kind}→{inherited_kind}), "
                                f"added recipient(s) "
                                f"{', '.join(missing_inherited)}, and "
                                f"re-encrypted {count} entr"
                                f"{'y' if count == 1 else 'ies'} "
                                f"(new meta pubkey: {new_meta_pubkey})"
                            )
                        else:
                            count = _repair_recipients(
                                backend,
                                meta_key,
                                local_recipients + missing_inherited,
                            )
                            repairs.append(
                                f"added recipient(s) "
                                f"{', '.join(missing_inherited)} and "
                                f"re-encrypted {count} entr"
                                f"{'y' if count == 1 else 'ies'}"
                            )
                    except (MetaKeyError, ShellOutError) as exc:
                        failures.append(f"repair: {exc}")
                    else:
                        issues = [i for i in issues if "inherited recipient" not in i]

                if issues:
                    failures.extend(issues)
                else:
                    print("  OK", file=sys.stdout)

    # Check 3: every entry decrypts.
    if meta_key is not None:
        print("Checking entry decryption ...", file=sys.stdout)
        decrypted: dict[str, str] = {}
        for tool in backend.list_tools():
            try:
                blob = meta_key.decrypt(backend.store_dir, tool, backend.extension)
                decrypted[tool] = blob
            except MetaKeyError as exc:
                failures.append(f"entry {tool} failed to decrypt: {exc}")
        if all(t in decrypted for t in backend.list_tools()):
            print(f"  OK ({len(decrypted)} entries)", file=sys.stdout)

        # Check 4: include graph well-formed.
        print("Checking include graph ...", file=sys.stdout)
        include_failures: list[str] = []
        for tool in backend.list_tools():
            try:
                resolve_includes(backend, tool, marker_loaded=set(), meta_key=meta_key)
            except IncludeError as exc:
                include_failures.append(f"include graph for {tool}: {exc}")
        if include_failures:
            failures.extend(include_failures)
        else:
            print("  OK", file=sys.stdout)

    if repairs:
        print("\nRepairs applied:", file=sys.stdout)
        for r in repairs:
            print(f"  - {r}", file=sys.stdout)

    if failures:
        print("\nDoctor found issues:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1

    print("\nAll checks passed.", file=sys.stdout)
    return 0


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

    # The gpg meta key materializes a temp GNUPGHOME that must not survive the
    # process. The try/finally covers the normal and exception paths; install
    # SIGINT/SIGTERM handlers so an interrupt mid-decrypt cleans up too (the age
    # and no-meta paths need none of this, so only arm it for gpg).
    needs_signal_cleanup = meta_key is not None and meta_key.backend == "gpg"
    prev_int = None
    prev_term = None
    if needs_signal_cleanup:

        def _handle_signal(signum: int, _frame: FrameType | None) -> None:
            if meta_key is not None:
                meta_key.cleanup()
            signal.signal(signum, signal.SIG_DFL)
            os.kill(os.getpid(), signum)

        prev_int = signal.signal(signal.SIGINT, _handle_signal)
        prev_term = signal.signal(signal.SIGTERM, _handle_signal)

    try:
        resolved = resolve_includes(
            backend, secret_key, marker_loaded, meta_key=meta_key
        )
    except IncludeError as exc:
        print(f"secwrap: {exc}", file=sys.stderr)
        return 1
    finally:
        if meta_key is not None:
            meta_key.cleanup()
        if needs_signal_cleanup:
            signal.signal(signal.SIGINT, prev_int)
            signal.signal(signal.SIGTERM, prev_term)
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
