# `secwrap` Phase 2b (passage meta key + bootstrap/rotate-meta/doctor) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate per-include TouchID prompts on the passage backend by introducing an in-memory age meta key (1 prompt per outer wrap, 0 prompts per include). Ship the three subcommands needed to manage the meta key lifecycle: `bootstrap`, `rotate-meta`, `doctor`. After this phase, a Claude session that wraps `claude` (with includes for `pnpm`, `docker`, `aws`) prompts once for the meta key, then decrypts all three include entries silently in-process.

**Architecture:**
- `MetaKey` (frozen dataclass) holds the decrypted age private key in process memory and exposes `decrypt(store_dir, entry, extension) -> str`.
- `load_meta_key(backend)` retrieves and parses `config/env-meta` (JSON: `{"backend": "age", "key": "AGE-SECRET-KEY-1..."}`); returns `None` if missing.
- `resolve_includes` gains a `meta_key: MetaKey | None` parameter; when set, it uses `meta_key.decrypt()` instead of `backend.show()` for each entry.
- `main()` calls `load_meta_key` once before include resolution; on fallback (no meta), emits a one-time stderr warning if the resolved set is non-trivial.
- Subcommands (`bootstrap`, `rotate-meta`, `doctor`) are reserved names. `secwrap -- <name>` forces wrap mode for binaries that collide with these names.
- All subcommands gate on the passage backend; pass backend versions wait for Phase 3.

**Tech stack:** Python 3.12, pytest, pytest-mock. Shells out to `age`, `age-keygen`, `passage`, `jq`, `shred` — all mocked in unit tests.

**Spec reference:** `docs/specs/2026-05-07-secwrap-includes-design.md` — sections "Meta Entry", "Bootstrap", "Rotation", "Failure Modes", "Security Considerations".

**Out of scope (deferred to Phase 3):**
- pass backend gpg meta-key flow (temp `$GNUPGHOME`, random passphrase).
- pass-backend implementations of `bootstrap`, `rotate-meta`, `doctor`.

---

## Plan decisions (locking down design ambiguities)

1. **`--` ends shim-flag parsing AND disables subcommand interpretation.** `parse_args` consumes `--` and sets `force_wrap: bool` on `Args`. `main()` skips subcommand dispatch when `force_wrap` is true. So `secwrap bootstrap` runs the subcommand; `secwrap -- bootstrap` wraps a binary called `bootstrap`. This is a deliberate departure from Phase 1's "`--` becomes the command name" deviation — the new behavior is what the spec implied all along.
2. **Reserved subcommand names: `bootstrap`, `rotate-meta`, `doctor`.** Hardcoded set. Any other first-token value goes through the wrap path.
3. **Subcommands are passage-only in Phase 2b.** On the pass backend, each prints `secwrap: <subcommand> is not yet supported for the pass backend (will arrive in Phase 3)` and exits 1.
4. **`MetaKey` holds the age private key as `bytes`, not `str`.** Lifecycle: created by `load_meta_key`, used by `resolve_includes` and the subcommand handlers, scoped in a `try/finally` block in `main()` that nulls the reference before exec. Python's GC means we can't guarantee zeroing, but we can avoid leaving live references through exec'd children.
5. **Fallback warning fires only if at least 2 entries would be decrypted.** Single-entry resolution (no includes) is unavoidably 1 prompt regardless of meta key presence — warning would be noise. Threshold: `len([n for n, _ in resolved if blob is not None]) >= 2 and meta_key is None`. Message: `secwrap: meta key absent; N includes will require N prompts (run \`secwrap bootstrap\` to fix)` where N is the number of entries to decrypt.
6. **`rotate-meta` requires `--yes`.** Without it, `secwrap rotate-meta` prints a description of what will happen and exits 0. With `--yes`, it proceeds. Rationale: a partial failure mid-rotation could leave entries unreadable; the user should opt in explicitly.
7. **`doctor` exits 0 if all checks pass, 1 if any fail.** Output goes to stdout (the report) and stderr (drift findings). Always prints; no `--quiet` mode in Phase 2b.
8. **`bootstrap` orchestration uses `subprocess.run` with explicit shell-outs**, not a pipeline. Each step is a separate `subprocess.run` call so failures get clean error messages. Temp files use `tempfile.NamedTemporaryFile` with `delete=False` and explicit `os.unlink` in `finally` — no shell `mktemp`.
9. **`shred` is best-effort, not required.** If `shred` isn't on PATH, fall back to `os.unlink` with a stderr note. The age key was already on disk briefly; both options are imperfect.
10. **Recipient list manipulation: read-modify-write under a lock.** `add_recipient(store_dir, pubkey)` reads `.age-recipients`, dedupes, appends, writes via `tempfile + os.replace` for atomicity. No flock — concurrent secwrap subcommand invocations are vanishingly rare.
11. **Subcommand handlers return `int` (exit code), not `None`.** Mirrors `main()`'s return type. 0 on success, 1 on user-facing failure. Internal failures (subprocess crashes, JSON parse errors) raise; `main()`'s top-level handler catches and prints.

---

## File structure

| Path                                  | Action  | Responsibility                                                                                                                 |
|---------------------------------------|---------|--------------------------------------------------------------------------------------------------------------------------------|
| `src/wlrenv/secwrap.py`               | Modify  | Add `MetaKey`, `MetaKeyError`, `load_meta_key`, `add_recipient`, `passage_reencrypt`, subcommand handlers (`do_bootstrap`, `do_rotate_meta`, `do_doctor`); rework `parse_args` for `--`; rework `main()` for meta-key load + subcommand dispatch. |
| `tests/test_secwrap.py`               | Modify  | Tests for all new code. Mock `subprocess.run` for shell-outs; one optional integration test using real `age` for `MetaKey.decrypt`. |
| `env.bash`                            | Modify  | Update alias template to `secwrap -- $cmd`.                                                                                    |
| `xonshrc.py`                          | Modify  | Update alias template to `secwrap -- {cmd}`.                                                                                   |

`secwrap.py` will grow from ~370 to ~600 lines. If it crosses 700 by end of phase, consider splitting in Phase 3 (`secwrap/parsers.py`, `secwrap/resolver.py`, `secwrap/subcommands.py`). Not splitting in Phase 2b because the file is still readable top-to-bottom and a split mid-feature creates churn.

---

## Tasks

### Task 1: `--` force-wrap in `parse_args`

Change `parse_args` so `--` is consumed (not stored as command) and sets `force_wrap: bool` on `Args`. The wrap-path-vs-subcommand decision in `main()` consults this flag.

**Files:**
- Modify: `src/wlrenv/secwrap.py`
- Modify: `tests/test_secwrap.py`

- [ ] **Step 1: Append failing tests for the new `--` semantics**

Append to `tests/test_secwrap.py`:

```python
def test_parse_args_double_dash_sets_force_wrap_and_consumes() -> None:
    args = parse_args(["--", "bootstrap", "arg1"])
    assert args.force_wrap is True
    assert args.command == "bootstrap"
    assert args.forwarded == ["arg1"]


def test_parse_args_double_dash_after_from() -> None:
    args = parse_args(["--from", "claude", "--", "doctor"])
    assert args.force_wrap is True
    assert args.from_name == "claude"
    assert args.command == "doctor"


def test_parse_args_double_dash_alone_is_error() -> None:
    # `secwrap --` with no command after: USAGE error path (no command).
    args = parse_args(["--"])
    assert args.force_wrap is True
    assert args.command is None


def test_parse_args_no_double_dash_force_wrap_false() -> None:
    args = parse_args(["claude"])
    assert args.force_wrap is False
    assert args.command == "claude"
```

Also: any existing test that asserts `parse_args(["--"]).command == "--"` (the old Phase 1 deviation) must be updated. Search the test file for `command == "--"` and adjust those tests to the new behavior, or delete them if they exclusively asserted the old deviation. Keep a single test asserting the new behavior (test_parse_args_double_dash_alone_is_error covers it).

- [ ] **Step 2: Run tests; confirm new tests fail**

Run: `cd /home/wlritchi/.wlrenv && uv run --group dev pytest tests/test_secwrap.py -v -k double_dash`
Expected: 4 new tests fail with `AttributeError: 'Args' object has no attribute 'force_wrap'`.

- [ ] **Step 3: Implement**

In `src/wlrenv/secwrap.py`:

1. Add `force_wrap: bool = False` to the `Args` dataclass (preserve field order; field defaults must keep frozen-dataclass init compatibility).

2. Rewrite the `--` branch in `parse_args`:

```python
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
```

The previous deviation (`elif a == "--" or not a.startswith("-"): break`) is removed. After `--`, no further shim flags are accepted (which is correct — flags before `--` only).

- [ ] **Step 4: Run tests; confirm all pass**

Run: `cd /home/wlritchi/.wlrenv && uv run --group dev pytest tests/test_secwrap.py -v`
Expected: 86 passed (82 prior + 4 new). If any old test fails because it asserted the deviated behavior, fix that test (update to the new `force_wrap` semantics) and re-run.

- [ ] **Step 5: Run autoformatter and pyright**

Run: `cd /home/wlritchi/.wlrenv && uv tool run ruff format src/wlrenv/secwrap.py tests/test_secwrap.py`
Run: `cd /home/wlritchi/.wlrenv && uv run --group dev pyright src/wlrenv/secwrap.py`
Expected: 0 errors.

- [ ] **Step 6: Commit**

```bash
cd /home/wlritchi/.wlrenv && git add src/wlrenv/secwrap.py tests/test_secwrap.py && git commit -m "$(cat <<'EOF'
feat(secwrap): -- ends flag parsing and disables subcommand dispatch

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: `MetaKey`, `MetaKeyError`, `load_meta_key`

Introduce the meta-key data type and the loader that reads `config/env-meta`. The loader is backend-agnostic (it uses `Backend.show`); validation is per-backend (the JSON `backend` field must match the runtime backend).

**Files:**
- Modify: `src/wlrenv/secwrap.py`
- Modify: `tests/test_secwrap.py`

- [ ] **Step 1: Append failing tests**

Append to `tests/test_secwrap.py`:

```python
from wlrenv.secwrap import MetaKey, MetaKeyError, load_meta_key


def test_load_meta_key_missing_returns_none(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    backend = _make_passage_backend(tmp_path)
    mocker.patch.object(Backend, "show", return_value=None)
    assert load_meta_key(backend) is None


def test_load_meta_key_valid_json_age(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    backend = _make_passage_backend(tmp_path)
    blob = '{"backend": "age", "key": "AGE-SECRET-KEY-1FAKE"}\n'
    mocker.patch.object(Backend, "show", return_value=blob)
    mk = load_meta_key(backend)
    assert mk is not None
    assert mk.backend == "age"
    assert mk.key == b"AGE-SECRET-KEY-1FAKE"


def test_load_meta_key_invalid_json_raises(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    backend = _make_passage_backend(tmp_path)
    mocker.patch.object(Backend, "show", return_value="not json")
    with pytest.raises(MetaKeyError, match=r"config/env-meta is not valid JSON"):
        load_meta_key(backend)


def test_load_meta_key_backend_mismatch_raises(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    backend = _make_passage_backend(tmp_path)
    blob = '{"backend": "gpg", "key": "...", "passphrase": "..."}'
    mocker.patch.object(Backend, "show", return_value=blob)
    with pytest.raises(
        MetaKeyError, match=r"declares backend=gpg but detected backend is passage"
    ):
        load_meta_key(backend)


def test_load_meta_key_missing_required_field_raises(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    backend = _make_passage_backend(tmp_path)
    blob = '{"backend": "age"}'  # no "key"
    mocker.patch.object(Backend, "show", return_value=blob)
    with pytest.raises(MetaKeyError, match=r"missing required field 'key'"):
        load_meta_key(backend)


def test_meta_key_decrypt_invokes_age(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    mk = MetaKey(backend="age", key=b"AGE-SECRET-KEY-1FAKE")
    run_mock = mocker.patch(
        "subprocess.run",
        return_value=subprocess.CompletedProcess(
            args=[], returncode=0, stdout="FOO=bar\n", stderr=""
        ),
    )
    result = mk.decrypt(tmp_path, "claude", "age")
    assert result == "FOO=bar\n"
    run_mock.assert_called_once()
    call = run_mock.call_args
    assert call.args[0][:3] == ["age", "-d", "--identity"]
    assert call.kwargs["input"] == b"AGE-SECRET-KEY-1FAKE"
    # Path arg should be the entry's full path:
    assert call.args[0][-1] == str(tmp_path / "config/env/claude.age")


def test_meta_key_decrypt_failure_raises(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    mk = MetaKey(backend="age", key=b"AGE-SECRET-KEY-1FAKE")
    mocker.patch(
        "subprocess.run",
        return_value=subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="age: bad key\n"
        ),
    )
    with pytest.raises(MetaKeyError, match=r"age decryption failed"):
        mk.decrypt(tmp_path, "claude", "age")
```

Add `import subprocess` to test imports if not already present (Phase 1 used it in tests indirectly via mocker; check first).

- [ ] **Step 2: Run tests; confirm new tests fail**

Run: `cd /home/wlritchi/.wlrenv && uv run --group dev pytest tests/test_secwrap.py -v -k "load_meta_key or meta_key_decrypt"`
Expected: 7 new tests fail with `ImportError`.

- [ ] **Step 3: Implement**

Add to `src/wlrenv/secwrap.py` (after `Backend`, before `IncludeError`):

```python
import json


class MetaKeyError(RuntimeError):
    """Raised when meta-key load, parse, or decryption fails."""


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
            raise MetaKeyError(
                f"MetaKey.decrypt: unsupported backend {self.backend!r}"
            )
        path = store_dir / "config" / "env" / f"{entry}.{extension}"
        result = subprocess.run(  # noqa: S603 - trusted binary, controlled args
            ["age", "-d", "--identity", "/dev/stdin", str(path)],
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
    return MetaKey(backend=declared, key=data["key"].encode("utf-8"))
```

Also add `import json` at the top of the file (alphabetical with other stdlib imports).

- [ ] **Step 4: Run tests; confirm all pass**

Run: `cd /home/wlritchi/.wlrenv && uv run --group dev pytest tests/test_secwrap.py -v`
Expected: 93 passed (86 prior + 7 new).

- [ ] **Step 5: Run autoformatter and pyright**

Run: `cd /home/wlritchi/.wlrenv && uv tool run ruff format src/wlrenv/secwrap.py tests/test_secwrap.py`
Run: `cd /home/wlritchi/.wlrenv && uv run --group dev pyright src/wlrenv/secwrap.py`
Expected: 0 errors.

- [ ] **Step 6: Commit**

```bash
cd /home/wlritchi/.wlrenv && git add src/wlrenv/secwrap.py tests/test_secwrap.py && git commit -m "$(cat <<'EOF'
feat(secwrap): MetaKey type + load_meta_key for in-process age decryption

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: `resolve_includes` accepts `meta_key`; `main()` integration with fallback warning

Wire the meta key into the wrap path. `resolve_includes` gains a `meta_key` parameter; when set, decryption goes through `meta_key.decrypt()` instead of `backend.show()`. `main()` calls `load_meta_key` once before resolution; emits a one-time stderr warning if the meta is absent and the resolved set has 2+ entries.

**Files:**
- Modify: `src/wlrenv/secwrap.py`
- Modify: `tests/test_secwrap.py`

- [ ] **Step 1: Append failing tests**

Append to `tests/test_secwrap.py`:

```python
def test_resolve_includes_uses_meta_key_when_provided(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    backend = _make_passage_backend(tmp_path)
    mk = MetaKey(backend="age", key=b"FAKEKEY")
    blobs = {
        "claude": "# secwrap-include: docker\nFOO=claude\n",
        "docker": "BAR=docker\n",
    }
    decrypt_mock = mocker.patch.object(
        MetaKey, "decrypt", side_effect=lambda _store, name, _ext: blobs[name]
    )
    show_mock = mocker.patch.object(Backend, "show")

    result = resolve_includes(backend, "claude", marker_loaded=set(), meta_key=mk)

    # Should have used decrypt, not show.
    assert decrypt_mock.call_count == 2
    show_mock.assert_not_called()
    names = [n for n, _ in result]
    assert names == ["docker", "claude"]


def test_resolve_includes_meta_key_none_falls_back_to_show(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    # meta_key=None: behavior identical to Phase 2a.
    backend = _make_passage_backend(tmp_path)
    blobs = {"config/env/claude": "FOO=bar\n"}
    show_mock = mocker.patch.object(
        Backend, "show", side_effect=lambda p: blobs.get(p)
    )

    result = resolve_includes(backend, "claude", marker_loaded=set(), meta_key=None)

    show_mock.assert_called_once_with("config/env/claude")
    assert result == [("claude", "FOO=bar\n")]


def test_main_loads_meta_key_and_uses_it(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    blobs = {
        "config/env-meta": '{"backend": "age", "key": "FAKE"}',
    }
    decrypt_blobs = {
        "claude": "# secwrap-include: docker\nKEY=claude\n",
        "docker": "DOCKER=yes\n",
    }
    mocker.patch.dict(
        "os.environ",
        {"SECWRAP_BACKEND": "passage", "PASSAGE_DIR": str(tmp_path), "PATH": "/usr/bin"},
        clear=True,
    )
    mocker.patch(
        "shutil.which",
        side_effect=lambda name: f"/usr/bin/{name}" if name in {"pass", "passage"} else None,
    )
    mocker.patch.object(Backend, "show", side_effect=lambda p: blobs.get(p))
    mocker.patch.object(
        MetaKey, "decrypt", side_effect=lambda _store, name, _ext: decrypt_blobs[name]
    )
    execvpe = mocker.patch("os.execvpe")

    main(["claude"])

    execvpe.assert_called_once()
    _file, _argv, env_arg = execvpe.call_args.args
    assert env_arg["KEY"] == "claude"
    assert env_arg["DOCKER"] == "yes"
    assert env_arg["_SECWRAP_LOADED"] == "claude:docker"


def test_main_warns_when_meta_absent_and_includes_present(
    mocker: MockerFixture, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    # meta entry missing, but claude has includes -> warn.
    blobs = {
        "config/env-meta": None,
        "config/env/claude": "# secwrap-include: docker\nKEY=claude\n",
        "config/env/docker": "DOCKER=yes\n",
    }
    mocker.patch.dict(
        "os.environ",
        {"SECWRAP_BACKEND": "passage", "PASSAGE_DIR": str(tmp_path), "PATH": "/usr/bin"},
        clear=True,
    )
    mocker.patch(
        "shutil.which",
        side_effect=lambda name: f"/usr/bin/{name}" if name in {"pass", "passage"} else None,
    )
    mocker.patch.object(Backend, "show", side_effect=lambda p: blobs.get(p))
    mocker.patch("os.execvpe")

    main(["claude"])

    err = capsys.readouterr().err
    assert "meta key absent" in err
    assert "2 includes" in err  # claude + docker = 2 entries
    assert "secwrap bootstrap" in err


def test_main_no_warn_for_single_entry_no_meta(
    mocker: MockerFixture, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    # Single entry, no includes — meta absent shouldn't warn.
    blobs = {
        "config/env-meta": None,
        "config/env/claude": "FOO=bar\n",
    }
    mocker.patch.dict(
        "os.environ",
        {"SECWRAP_BACKEND": "passage", "PASSAGE_DIR": str(tmp_path), "PATH": "/usr/bin"},
        clear=True,
    )
    mocker.patch(
        "shutil.which",
        side_effect=lambda name: f"/usr/bin/{name}" if name in {"pass", "passage"} else None,
    )
    mocker.patch.object(Backend, "show", side_effect=lambda p: blobs.get(p))
    mocker.patch("os.execvpe")

    main(["claude"])

    assert capsys.readouterr().err == ""


def test_main_meta_key_error_exits_one(
    mocker: MockerFixture, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    # Malformed meta entry: hard error, exit 1, don't exec.
    blobs = {
        "config/env-meta": "not json",
    }
    mocker.patch.dict(
        "os.environ",
        {"SECWRAP_BACKEND": "passage", "PASSAGE_DIR": str(tmp_path), "PATH": "/usr/bin"},
        clear=True,
    )
    mocker.patch(
        "shutil.which",
        side_effect=lambda name: f"/usr/bin/{name}" if name in {"pass", "passage"} else None,
    )
    mocker.patch.object(Backend, "show", side_effect=lambda p: blobs.get(p))
    execvpe = mocker.patch("os.execvpe")

    rc = main(["claude"])

    assert rc == 1
    execvpe.assert_not_called()
    err = capsys.readouterr().err
    assert "config/env-meta is not valid JSON" in err
```

- [ ] **Step 2: Run tests; confirm new tests fail**

Run: `cd /home/wlritchi/.wlrenv && uv run --group dev pytest tests/test_secwrap.py -v`
Expected: previous 93 still pass; 6 new tests fail (some with TypeError on the meta_key kwarg, others with assertion failures).

- [ ] **Step 3: Implement**

In `src/wlrenv/secwrap.py`:

1. Update `resolve_includes` signature to accept `meta_key: MetaKey | None = None`. Update the body so the `backend.show(f"config/env/{name}")` calls become:

```python
            if meta_key is not None:
                try:
                    blob = meta_key.decrypt(backend.store_dir, name, backend.extension)
                except MetaKeyError:
                    blob = None
            else:
                blob = backend.show(f"config/env/{name}")
```

The `pass` branch at the top of `resolve_includes` is unchanged (pass backend has no meta key in Phase 2b). Update its docstring to mention the new param.

2. Rework `main()`'s wrap branch (lines ~313-371) to load the meta key and pass it through. After backend detection and before `resolve_includes`:

```python
    try:
        meta_key = load_meta_key(backend)
    except MetaKeyError as exc:
        print(f"secwrap: {exc}", file=sys.stderr)
        return 1

    try:
        resolved = resolve_includes(backend, secret_key, marker_loaded, meta_key=meta_key)
    except IncludeError as exc:
        print(f"secwrap: {exc}", file=sys.stderr)
        return 1
    finally:
        # Drop the live reference to the key bytes before exec.
        meta_key = None
```

Wait — that `finally` runs after `resolve_includes` succeeds, but we still need `meta_key` later? No — once resolution is complete, the blobs are in memory and the meta key isn't needed for env merging or exec. The `finally` correctly releases it.

Actually, there's a subtle issue: if we set `meta_key = None` in `finally`, that re-binds the local but does nothing about the bytes that were copied into `subprocess.run` calls. Best we can do in Python. The point is to not leak the reference into env / children.

3. Add the fallback warning logic (after `resolved` is built, before exec):

```python
    decrypt_count = sum(1 for _name, blob in resolved if blob is not None)
    if meta_key is None and decrypt_count >= 2:
        print(
            f"secwrap: meta key absent; {decrypt_count} includes will require "
            f"{decrypt_count} prompts (run `secwrap bootstrap` to fix)",
            file=sys.stderr,
        )
```

But wait — by this point we already set `meta_key = None` in the `finally`. Move the warning logic BEFORE the `finally` block, or compute it via a separate flag captured before the `finally`. Cleaner: track `meta_was_absent: bool` before the resolve, use it in the warning.

Cleanest structure:

```python
    try:
        meta_key = load_meta_key(backend)
    except MetaKeyError as exc:
        print(f"secwrap: {exc}", file=sys.stderr)
        return 1

    meta_was_absent = meta_key is None

    try:
        resolved = resolve_includes(backend, secret_key, marker_loaded, meta_key=meta_key)
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

    # ... env merge and exec as before ...
```

- [ ] **Step 4: Run tests; confirm all pass**

Run: `cd /home/wlritchi/.wlrenv && uv run --group dev pytest tests/test_secwrap.py -v`
Expected: 99 passed (93 prior + 6 new).

- [ ] **Step 5: Run autoformatter, pyright, ruff**

Run: `cd /home/wlritchi/.wlrenv && uv tool run ruff format src/wlrenv/secwrap.py tests/test_secwrap.py`
Run: `cd /home/wlritchi/.wlrenv && uv run --group dev pyright src/wlrenv/secwrap.py`
Run: `cd /home/wlritchi/.wlrenv && uv run --group dev ruff check src/wlrenv/secwrap.py tests/test_secwrap.py`
Expected: 0 errors.

- [ ] **Step 6: Commit**

```bash
cd /home/wlritchi/.wlrenv && git add src/wlrenv/secwrap.py tests/test_secwrap.py && git commit -m "$(cat <<'EOF'
feat(secwrap): wrap path uses meta key for in-process include decryption

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Subcommand dispatch skeleton

Wire reserved subcommand names (`bootstrap`, `rotate-meta`, `doctor`) into `main()` ahead of the wrap path. Stub each handler with a `NotImplementedError` for now; Tasks 5-7 fill them in. Pass-backend gating goes in this task (since all three subcommands share the same gate).

**Files:**
- Modify: `src/wlrenv/secwrap.py`
- Modify: `tests/test_secwrap.py`

- [ ] **Step 1: Append failing tests**

Append to `tests/test_secwrap.py`:

```python
def test_main_bootstrap_dispatches(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch.dict(
        "os.environ",
        {"SECWRAP_BACKEND": "passage", "PASSAGE_DIR": str(tmp_path), "PATH": "/usr/bin"},
        clear=True,
    )
    mocker.patch(
        "shutil.which",
        side_effect=lambda name: f"/usr/bin/{name}" if name in {"pass", "passage"} else None,
    )
    do_bootstrap = mocker.patch("wlrenv.secwrap.do_bootstrap", return_value=0)

    rc = main(["bootstrap"])

    assert rc == 0
    do_bootstrap.assert_called_once()


def test_main_rotate_meta_dispatches(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch.dict(
        "os.environ",
        {"SECWRAP_BACKEND": "passage", "PASSAGE_DIR": str(tmp_path), "PATH": "/usr/bin"},
        clear=True,
    )
    mocker.patch(
        "shutil.which",
        side_effect=lambda name: f"/usr/bin/{name}" if name in {"pass", "passage"} else None,
    )
    do_rotate = mocker.patch("wlrenv.secwrap.do_rotate_meta", return_value=0)

    rc = main(["rotate-meta", "--yes"])

    assert rc == 0
    do_rotate.assert_called_once()
    # Args struct should preserve the --yes
    call_args = do_rotate.call_args
    # First positional is backend; second is args.forwarded (or similar)
    assert "--yes" in call_args.args[1] or call_args.args[1] == ["--yes"]


def test_main_doctor_dispatches(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch.dict(
        "os.environ",
        {"SECWRAP_BACKEND": "passage", "PASSAGE_DIR": str(tmp_path), "PATH": "/usr/bin"},
        clear=True,
    )
    mocker.patch(
        "shutil.which",
        side_effect=lambda name: f"/usr/bin/{name}" if name in {"pass", "passage"} else None,
    )
    do_doctor = mocker.patch("wlrenv.secwrap.do_doctor", return_value=0)

    rc = main(["doctor"])

    assert rc == 0
    do_doctor.assert_called_once()


def test_main_subcommand_pass_backend_exits_one(
    mocker: MockerFixture, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    mocker.patch.dict(
        "os.environ",
        {
            "SECWRAP_BACKEND": "pass",
            "PASSWORD_STORE_DIR": str(tmp_path),
            "PATH": "/usr/bin",
        },
        clear=True,
    )
    mocker.patch(
        "shutil.which",
        side_effect=lambda name: f"/usr/bin/{name}" if name in {"pass", "passage"} else None,
    )
    do_bootstrap = mocker.patch("wlrenv.secwrap.do_bootstrap")

    rc = main(["bootstrap"])

    assert rc == 1
    do_bootstrap.assert_not_called()
    err = capsys.readouterr().err
    assert "not yet supported for the pass backend" in err
    assert "Phase 3" in err


def test_main_force_wrap_skips_subcommand(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    # `secwrap -- bootstrap` wraps a binary called bootstrap, doesn't dispatch.
    mocker.patch.dict(
        "os.environ",
        {"SECWRAP_BACKEND": "passage", "PASSAGE_DIR": str(tmp_path), "PATH": "/usr/bin"},
        clear=True,
    )
    mocker.patch(
        "shutil.which",
        side_effect=lambda name: f"/usr/bin/{name}" if name in {"pass", "passage"} else None,
    )
    mocker.patch.object(Backend, "show", return_value=None)  # no meta, no entry
    do_bootstrap = mocker.patch("wlrenv.secwrap.do_bootstrap")
    execvpe = mocker.patch("os.execvpe")

    main(["--", "bootstrap", "arg"])

    do_bootstrap.assert_not_called()
    execvpe.assert_called_once()
    file_arg, argv_arg, _ = execvpe.call_args.args
    assert file_arg == "bootstrap"
    assert argv_arg == ["bootstrap", "arg"]
```

- [ ] **Step 2: Run tests; confirm new tests fail**

Run: `cd /home/wlritchi/.wlrenv && uv run --group dev pytest tests/test_secwrap.py -v`
Expected: previous 99 still pass; 5 new tests fail.

- [ ] **Step 3: Implement**

In `src/wlrenv/secwrap.py`:

1. Add the reserved set as a module-level constant near `USAGE`:

```python
_SUBCOMMANDS = frozenset({"bootstrap", "rotate-meta", "doctor"})
```

2. Add stub subcommand handlers (after `resolve_includes`, before `main`):

```python
def do_bootstrap(backend: Backend, args: list[str]) -> int:
    raise NotImplementedError("bootstrap: implemented in Task 5")


def do_rotate_meta(backend: Backend, args: list[str]) -> int:
    raise NotImplementedError("rotate-meta: implemented in Task 6")


def do_doctor(backend: Backend, args: list[str]) -> int:
    raise NotImplementedError("doctor: implemented in Task 7")
```

3. Insert subcommand dispatch into `main()` AFTER backend detection (so the pass-backend gate has the backend object) but BEFORE the marker short-circuit (subcommands shouldn't be skipped by the marker — they're not wraps). Specifically, the order in `main()` becomes:

```
parse_args → help/list/no-command short-circuits → backend detection
  → if not args.force_wrap and args.command in _SUBCOMMANDS:
        if backend.name != "passage":
            print(...); return 1
        dispatch to do_bootstrap / do_rotate_meta / do_doctor
        return their result
  → marker short-circuit
  → wrap path (load meta, resolve, merge, exec)
```

This means moving the `Backend.detect()` call earlier in `main()` than it is today. The current order has:
1. parse → help/list/no-command checks
2. Marker short-circuit (pre-backend, for the cheap path)
3. Backend detect
4. List mode
5. Wrap path

The new order:
1. parse → help/list/no-command checks
2. Backend detect (now early, since subcommands need it)
3. List mode (this is also gated on `args.list_mode` so position is fine)
4. Subcommand dispatch (if `args.command in _SUBCOMMANDS and not args.force_wrap`)
5. Marker short-circuit
6. Wrap path

The cost: marker short-circuit no longer fully avoids `Backend.detect()`. But `Backend.detect()` is just a `shutil.which` check + a directory existence test — no decryption, no prompts. It's cheap. The "cheap short-circuit" goal is preserved (no `backend.show` call).

Updated `main()` skeleton:

```python
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

    # Wrap path.
    secret_key = args.from_name if args.from_name is not None else args.command
    marker_loaded = parse_marker(os.environ.get("_SECWRAP_LOADED", ""))
    if secret_key in marker_loaded:
        os.execvpe(args.command, [args.command, *args.forwarded], os.environ)  # noqa: S606
        return 0

    # ... rest of wrap path unchanged (load meta key, resolve, merge, exec) ...
```

The marker short-circuit moves AFTER subcommand dispatch but stays before meta-key loading. Keep the rest of the wrap path as-is.

- [ ] **Step 4: Run tests; confirm all pass**

Run: `cd /home/wlritchi/.wlrenv && uv run --group dev pytest tests/test_secwrap.py -v`
Expected: 104 passed (99 prior + 5 new). Check that prior `test_main_wrap_short_circuits_*` tests still pass — they now go through `Backend.detect()` first.

- [ ] **Step 5: Run autoformatter, pyright, ruff**

Run: `cd /home/wlritchi/.wlrenv && uv tool run ruff format src/wlrenv/secwrap.py tests/test_secwrap.py`
Run: `cd /home/wlritchi/.wlrenv && uv run --group dev pyright src/wlrenv/secwrap.py`
Run: `cd /home/wlritchi/.wlrenv && uv run --group dev ruff check src/wlrenv/secwrap.py tests/test_secwrap.py`

- [ ] **Step 6: Commit**

```bash
cd /home/wlritchi/.wlrenv && git add src/wlrenv/secwrap.py tests/test_secwrap.py && git commit -m "$(cat <<'EOF'
feat(secwrap): subcommand dispatch skeleton (bootstrap, rotate-meta, doctor stubs)

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Implement `secwrap bootstrap`

Generates an age meta key, registers it with the passage store, re-encrypts existing entries, and inserts the meta entry. Six discrete shell-outs (per the spec):

1. `age-keygen -pq -o $tmpfile` (post-quantum if available; fall back via `age-keygen -o $tmpfile`)
2. `pubkey=$(age-keygen -y $tmpfile)`
3. Append pubkey to `.age-recipients` (idempotent)
4. `passage reencrypt config/env`
5. `jq -n --arg k "$(cat $tmpfile)" '{backend: "age", key: $k}' | passage insert -m config/env-meta`
6. `shred -u $tmpfile` (or `os.unlink` fallback)

Exit conditions:
- Pre-flight: error if `age-keygen` not on PATH; error if `passage` not on PATH; error if `config/env-meta` already exists (use `rotate-meta` instead).
- Each shell-out wrapped in `try`/error message on non-zero exit.
- Cleanup of `$tmpfile` even on failure.

**Files:**
- Modify: `src/wlrenv/secwrap.py`
- Modify: `tests/test_secwrap.py`

- [ ] **Step 1: Append failing tests**

Append to `tests/test_secwrap.py`:

```python
from wlrenv.secwrap import do_bootstrap


def test_do_bootstrap_happy_path(mocker: MockerFixture, tmp_path: Path) -> None:
    backend = _make_passage_backend(tmp_path)
    # Simulate passage store layout: store_dir/config/env/.age-recipients
    (tmp_path / "config" / "env").mkdir(parents=True)
    recipients_file = tmp_path / "config" / "env" / ".age-recipients"
    recipients_file.write_text("age1user...\n")

    mocker.patch.object(Backend, "show", return_value=None)  # no existing meta
    mocker.patch(
        "shutil.which",
        side_effect=lambda name: f"/usr/bin/{name}" if name in {"age-keygen", "passage", "shred"} else None,
    )

    # Mock subprocess.run for each shell-out.
    keygen_calls = {"count": 0}

    def fake_run(cmd, **kwargs):
        if cmd[0] == "age-keygen" and "-pq" in cmd:
            keygen_calls["count"] += 1
            # Write a fake key to the output file.
            out_idx = cmd.index("-o") + 1
            Path(cmd[out_idx]).write_text("AGE-SECRET-KEY-1FAKE\n")
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[0] == "age-keygen" and "-y" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "age1pub...\n", "")
        if cmd[0] == "passage" and cmd[1] == "reencrypt":
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[0] == "passage" and cmd[1] == "insert":
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[0] == "shred":
            return subprocess.CompletedProcess(cmd, 0, "", "")
        raise AssertionError(f"unexpected command: {cmd}")

    mocker.patch("subprocess.run", side_effect=fake_run)

    rc = do_bootstrap(backend, [])
    assert rc == 0
    # Recipients file now has both keys, deduped, alphabetically sorted lines preserved order.
    contents = recipients_file.read_text().splitlines()
    assert "age1user..." in contents
    assert "age1pub..." in contents


def test_do_bootstrap_meta_already_exists(
    mocker: MockerFixture, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    backend = _make_passage_backend(tmp_path)
    mocker.patch.object(Backend, "show", return_value='{"backend": "age", "key": "..."}')
    rc = do_bootstrap(backend, [])
    assert rc == 1
    err = capsys.readouterr().err
    assert "config/env-meta already exists" in err
    assert "rotate-meta" in err


def test_do_bootstrap_age_keygen_missing(
    mocker: MockerFixture, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    backend = _make_passage_backend(tmp_path)
    mocker.patch.object(Backend, "show", return_value=None)
    mocker.patch("shutil.which", return_value=None)  # nothing on PATH
    rc = do_bootstrap(backend, [])
    assert rc == 1
    err = capsys.readouterr().err
    assert "age-keygen not found" in err


def test_do_bootstrap_keygen_fallback_to_x25519(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    # First age-keygen with -pq fails (older age); second without -pq succeeds.
    backend = _make_passage_backend(tmp_path)
    (tmp_path / "config" / "env").mkdir(parents=True)
    (tmp_path / "config" / "env" / ".age-recipients").write_text("age1user\n")

    mocker.patch.object(Backend, "show", return_value=None)
    mocker.patch(
        "shutil.which",
        side_effect=lambda name: f"/usr/bin/{name}" if name in {"age-keygen", "passage"} else None,
    )

    def fake_run(cmd, **kwargs):
        if cmd[0] == "age-keygen" and "-pq" in cmd:
            return subprocess.CompletedProcess(cmd, 1, "", "unrecognized flag: -pq\n")
        if cmd[0] == "age-keygen" and "-o" in cmd:  # fallback without -pq
            out_idx = cmd.index("-o") + 1
            Path(cmd[out_idx]).write_text("AGE-SECRET-KEY-1FALLBACK\n")
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[0] == "age-keygen" and "-y" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "age1pub...\n", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    mocker.patch("subprocess.run", side_effect=fake_run)

    rc = do_bootstrap(backend, [])
    assert rc == 0


def test_do_bootstrap_reencrypt_failure_aborts(
    mocker: MockerFixture, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    backend = _make_passage_backend(tmp_path)
    (tmp_path / "config" / "env").mkdir(parents=True)
    recipients_file = tmp_path / "config" / "env" / ".age-recipients"
    recipients_file.write_text("age1user\n")

    mocker.patch.object(Backend, "show", return_value=None)
    mocker.patch(
        "shutil.which",
        side_effect=lambda name: f"/usr/bin/{name}" if name in {"age-keygen", "passage"} else None,
    )

    def fake_run(cmd, **kwargs):
        if cmd[0] == "age-keygen" and "-pq" in cmd:
            out_idx = cmd.index("-o") + 1
            Path(cmd[out_idx]).write_text("AGE-SECRET-KEY-1FAKE\n")
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[0] == "age-keygen" and "-y" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "age1pub\n", "")
        if cmd[0] == "passage" and cmd[1] == "reencrypt":
            return subprocess.CompletedProcess(cmd, 1, "", "passage: error\n")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    mocker.patch("subprocess.run", side_effect=fake_run)

    rc = do_bootstrap(backend, [])
    assert rc == 1
    err = capsys.readouterr().err
    assert "passage reencrypt failed" in err
```

- [ ] **Step 2: Run tests; confirm new tests fail**

Run: `cd /home/wlritchi/.wlrenv && uv run --group dev pytest tests/test_secwrap.py -v -k bootstrap`
Expected: 5 new tests fail (NotImplementedError or similar).

- [ ] **Step 3: Implement**

Add helper functions and `do_bootstrap` to `src/wlrenv/secwrap.py`. Replace the `do_bootstrap` stub.

```python
import tempfile


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
    tmp = recipients_path.with_suffix(".tmp")
    tmp.write_text(new_contents)
    tmp.replace(recipients_path)


def _run_or_fail(cmd: list[str], purpose: str, **kwargs) -> subprocess.CompletedProcess[str]:
    """Run `cmd`; raise MetaKeyError on non-zero exit, with a `purpose`-shaped message."""
    result = subprocess.run(  # noqa: S603 - trusted binary, controlled args
        cmd, capture_output=True, text=True, check=False, **kwargs
    )
    if result.returncode != 0:
        raise MetaKeyError(
            f"{purpose} failed: {result.stderr.strip() or result.stdout.strip()}"
        )
    return result


def do_bootstrap(backend: Backend, args: list[str]) -> int:
    # Pre-flight checks.
    if backend.show("config/env-meta") is not None:
        print(
            "secwrap: config/env-meta already exists; "
            "use `secwrap rotate-meta --yes` to replace it",
            file=sys.stderr,
        )
        return 1
    if shutil.which("age-keygen") is None:
        print("secwrap: age-keygen not found on PATH", file=sys.stderr)
        return 1
    if shutil.which("passage") is None:
        print("secwrap: passage not found on PATH", file=sys.stderr)
        return 1

    # Generate key.
    with tempfile.NamedTemporaryFile(
        mode="w", prefix="secwrap-meta-", suffix=".txt", delete=False
    ) as tf:
        keyfile = Path(tf.name)
    try:
        # Try -pq (post-quantum); fall back to default if unsupported.
        result = subprocess.run(  # noqa: S603
            ["age-keygen", "-pq", "-o", str(keyfile)],
            capture_output=True, text=True, check=False,
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

        # Insert meta entry. Build the JSON payload from the keyfile content.
        key_content = keyfile.read_text().strip()
        payload = json.dumps({"backend": "age", "key": key_content})
        _run_or_fail(
            ["passage", "insert", "-m", "config/env-meta"],
            "passage insert config/env-meta",
            input=payload + "\n",
        )

        print("secwrap: bootstrap complete", file=sys.stderr)
        return 0
    except MetaKeyError as exc:
        print(f"secwrap: {exc}", file=sys.stderr)
        return 1
    finally:
        # Best-effort secure deletion.
        if shutil.which("shred") is not None:
            subprocess.run(  # noqa: S603
                ["shred", "-u", str(keyfile)],
                capture_output=True, check=False,
            )
        if keyfile.exists():
            keyfile.unlink()
```

- [ ] **Step 4: Run tests; confirm all pass**

Run: `cd /home/wlritchi/.wlrenv && uv run --group dev pytest tests/test_secwrap.py -v`
Expected: 109 passed (104 prior + 5 new).

- [ ] **Step 5: Run autoformatter, pyright, ruff**

(Same as prior tasks.)

- [ ] **Step 6: Commit**

```bash
cd /home/wlritchi/.wlrenv && git add src/wlrenv/secwrap.py tests/test_secwrap.py && git commit -m "$(cat <<'EOF'
feat(secwrap): bootstrap subcommand for passage age meta key

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Implement `secwrap rotate-meta`

Procedure: generate new key, replace old recipient with new in `.age-recipients`, `passage reencrypt config/env`, replace `config/env-meta`. Requires `--yes`; otherwise prints a description and exits 0.

**Files:**
- Modify: `src/wlrenv/secwrap.py`
- Modify: `tests/test_secwrap.py`

- [ ] **Step 1: Append failing tests**

Append to `tests/test_secwrap.py`:

```python
from wlrenv.secwrap import do_rotate_meta


def test_do_rotate_meta_without_yes_describes(
    mocker: MockerFixture, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    backend = _make_passage_backend(tmp_path)
    mocker.patch.object(
        Backend, "show",
        return_value='{"backend": "age", "key": "AGE-SECRET-KEY-1OLD"}',
    )

    rc = do_rotate_meta(backend, [])

    assert rc == 0
    out = capsys.readouterr().out
    # Description goes to stdout
    assert "rotate-meta will" in out.lower() or "this will" in out.lower()
    assert "--yes" in out


def test_do_rotate_meta_with_yes_happy_path(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    backend = _make_passage_backend(tmp_path)
    (tmp_path / "config" / "env").mkdir(parents=True)
    recipients = tmp_path / "config" / "env" / ".age-recipients"
    recipients.write_text("age1user\nage1oldmeta\n")

    # Existing meta with old pubkey embedded — the rotate path needs to know
    # which recipient to remove. We derive it from age-keygen -y on the OLD
    # key, which we'll mock.
    old_blob = '{"backend": "age", "key": "AGE-SECRET-KEY-1OLD"}'
    mocker.patch.object(Backend, "show", return_value=old_blob)
    mocker.patch(
        "shutil.which",
        side_effect=lambda name: f"/usr/bin/{name}" if name in {"age-keygen", "passage"} else None,
    )

    def fake_run(cmd, **kwargs):
        if cmd[0] == "age-keygen" and "-pq" in cmd:
            out_idx = cmd.index("-o") + 1
            Path(cmd[out_idx]).write_text("AGE-SECRET-KEY-1NEW\n")
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[0] == "age-keygen" and "-y" in cmd:
            # Return different pubkeys for old vs new based on input file content.
            content = Path(cmd[-1]).read_text().strip()
            if "OLD" in content:
                return subprocess.CompletedProcess(cmd, 0, "age1oldmeta\n", "")
            return subprocess.CompletedProcess(cmd, 0, "age1newmeta\n", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    mocker.patch("subprocess.run", side_effect=fake_run)

    rc = do_rotate_meta(backend, ["--yes"])

    assert rc == 0
    contents = recipients.read_text().splitlines()
    assert "age1user" in contents
    assert "age1newmeta" in contents
    assert "age1oldmeta" not in contents


def test_do_rotate_meta_no_existing_meta(
    mocker: MockerFixture, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    backend = _make_passage_backend(tmp_path)
    mocker.patch.object(Backend, "show", return_value=None)

    rc = do_rotate_meta(backend, ["--yes"])

    assert rc == 1
    err = capsys.readouterr().err
    assert "no config/env-meta found" in err
    assert "bootstrap" in err
```

- [ ] **Step 2: Run tests; confirm new tests fail**

Run: `cd /home/wlritchi/.wlrenv && uv run --group dev pytest tests/test_secwrap.py -v -k rotate_meta`
Expected: 3 new tests fail.

- [ ] **Step 3: Implement**

Replace the `do_rotate_meta` stub:

```python
def _remove_age_recipient(store_dir: Path, pubkey: str) -> None:
    """Remove `pubkey` from `.age-recipients`. No-op if absent."""
    recipients_path = store_dir / "config" / "env" / ".age-recipients"
    if not recipients_path.exists():
        return
    lines = recipients_path.read_text().splitlines()
    filtered = [line for line in lines if line.strip() != pubkey]
    if filtered == lines:
        return
    new_contents = "\n".join(filtered) + ("\n" if filtered else "")
    tmp = recipients_path.with_suffix(".tmp")
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
        old_key = existing["key"]
    except (json.JSONDecodeError, KeyError) as exc:
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
        result = subprocess.run(  # noqa: S603
            ["age-keygen", "-pq", "-o", str(new_keyfile)],
            capture_output=True, text=True, check=False,
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

        # Update recipients atomically: remove old, add new.
        _remove_age_recipient(backend.store_dir, old_pubkey)
        _add_age_recipient(backend.store_dir, new_pubkey)

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
    except MetaKeyError as exc:
        print(f"secwrap: {exc}", file=sys.stderr)
        return 1
    finally:
        for path in (old_keyfile, new_keyfile):
            if path is None:
                continue
            if shutil.which("shred") is not None:
                subprocess.run(  # noqa: S603
                    ["shred", "-u", str(path)],
                    capture_output=True, check=False,
                )
            if path.exists():
                path.unlink()
```

Note the `--force` flag on `passage insert` to overwrite the existing meta entry without prompting.

- [ ] **Step 4: Run tests; confirm all pass**

Run: `cd /home/wlritchi/.wlrenv && uv run --group dev pytest tests/test_secwrap.py -v`
Expected: 112 passed (109 prior + 3 new).

- [ ] **Step 5: Run autoformatter, pyright, ruff**

- [ ] **Step 6: Commit**

```bash
cd /home/wlritchi/.wlrenv && git add src/wlrenv/secwrap.py tests/test_secwrap.py && git commit -m "$(cat <<'EOF'
feat(secwrap): rotate-meta subcommand

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Implement `secwrap doctor`

Read-only health check. Verifies:
1. `config/env-meta` exists and parses (JSON, correct backend, has `key`).
2. `.age-recipients` exists and contains the meta pubkey.
3. Every `config/env/*` entry decrypts under the meta key.
4. The include graph is well-formed (no cycles, no missing deps) for every entry.

Exit 0 if all clean; exit 1 if any check fails. Always prints a summary.

**Files:**
- Modify: `src/wlrenv/secwrap.py`
- Modify: `tests/test_secwrap.py`

- [ ] **Step 1: Append failing tests**

Append to `tests/test_secwrap.py`:

```python
from wlrenv.secwrap import do_doctor


def test_do_doctor_all_clean(
    mocker: MockerFixture, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    backend = _make_passage_backend(tmp_path)
    (tmp_path / "config" / "env").mkdir(parents=True)
    recipients = tmp_path / "config" / "env" / ".age-recipients"
    recipients.write_text("age1user\nage1meta\n")

    # Two entries.
    (tmp_path / "config" / "env" / "claude.age").write_bytes(b"fake-encrypted")
    (tmp_path / "config" / "env" / "docker.age").write_bytes(b"fake-encrypted")

    # show: meta entry; list_tools: claude, docker.
    blobs = {
        "config/env-meta": '{"backend": "age", "key": "AGE-SECRET-KEY-1FAKE"}',
    }
    mocker.patch.object(Backend, "show", side_effect=lambda p: blobs.get(p))
    decrypt_blobs = {
        "claude": "# secwrap-include: docker\nFOO=claude\n",
        "docker": "BAR=docker\n",
    }
    mocker.patch.object(
        MetaKey, "decrypt", side_effect=lambda _store, name, _ext: decrypt_blobs[name]
    )
    mocker.patch(
        "subprocess.run",
        return_value=subprocess.CompletedProcess([], 0, "age1meta\n", ""),
    )

    rc = do_doctor(backend, [])

    assert rc == 0
    out = capsys.readouterr().out
    assert "OK" in out or "✓" in out  # some indicator of pass


def test_do_doctor_missing_meta(
    mocker: MockerFixture, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    backend = _make_passage_backend(tmp_path)
    mocker.patch.object(Backend, "show", return_value=None)
    rc = do_doctor(backend, [])
    assert rc == 1
    err = capsys.readouterr().err
    assert "config/env-meta" in err


def test_do_doctor_recipient_drift(
    mocker: MockerFixture, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    backend = _make_passage_backend(tmp_path)
    (tmp_path / "config" / "env").mkdir(parents=True)
    # Recipient list MISSING the meta pubkey.
    (tmp_path / "config" / "env" / ".age-recipients").write_text("age1user\n")

    blobs = {"config/env-meta": '{"backend": "age", "key": "AGE-SECRET-KEY-1FAKE"}'}
    mocker.patch.object(Backend, "show", side_effect=lambda p: blobs.get(p))
    # age-keygen -y returns a pubkey not in the recipients file.
    mocker.patch(
        "subprocess.run",
        return_value=subprocess.CompletedProcess([], 0, "age1meta-missing\n", ""),
    )

    rc = do_doctor(backend, [])

    assert rc == 1
    err = capsys.readouterr().err
    assert "recipient" in err.lower()
    assert "age1meta-missing" in err


def test_do_doctor_entry_decrypt_failure(
    mocker: MockerFixture, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    backend = _make_passage_backend(tmp_path)
    (tmp_path / "config" / "env").mkdir(parents=True)
    (tmp_path / "config" / "env" / ".age-recipients").write_text("age1user\nage1meta\n")
    (tmp_path / "config" / "env" / "broken.age").write_bytes(b"fake")

    blobs = {"config/env-meta": '{"backend": "age", "key": "AGE-SECRET-KEY-1FAKE"}'}
    mocker.patch.object(Backend, "show", side_effect=lambda p: blobs.get(p))
    mocker.patch(
        "subprocess.run",
        return_value=subprocess.CompletedProcess([], 0, "age1meta\n", ""),
    )
    # Decrypt raises for the broken entry.
    mocker.patch.object(
        MetaKey, "decrypt",
        side_effect=MetaKeyError("age decryption failed for broken: bad MAC"),
    )

    rc = do_doctor(backend, [])

    assert rc == 1
    err = capsys.readouterr().err
    assert "broken" in err
    assert "decrypt" in err.lower()


def test_do_doctor_cycle_detected(
    mocker: MockerFixture, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    backend = _make_passage_backend(tmp_path)
    (tmp_path / "config" / "env").mkdir(parents=True)
    (tmp_path / "config" / "env" / ".age-recipients").write_text("age1user\nage1meta\n")
    (tmp_path / "config" / "env" / "a.age").write_bytes(b"fake")
    (tmp_path / "config" / "env" / "b.age").write_bytes(b"fake")

    blobs = {"config/env-meta": '{"backend": "age", "key": "AGE-SECRET-KEY-1FAKE"}'}
    mocker.patch.object(Backend, "show", side_effect=lambda p: blobs.get(p))
    mocker.patch(
        "subprocess.run",
        return_value=subprocess.CompletedProcess([], 0, "age1meta\n", ""),
    )
    decrypt_blobs = {
        "a": "# secwrap-include: b\n",
        "b": "# secwrap-include: a\n",
    }
    mocker.patch.object(
        MetaKey, "decrypt", side_effect=lambda _store, name, _ext: decrypt_blobs[name]
    )

    rc = do_doctor(backend, [])

    assert rc == 1
    err = capsys.readouterr().err
    assert "cycle" in err.lower()
```

- [ ] **Step 2: Run tests; confirm new tests fail**

Run: `cd /home/wlritchi/.wlrenv && uv run --group dev pytest tests/test_secwrap.py -v -k doctor`
Expected: 5 new tests fail.

- [ ] **Step 3: Implement**

Replace the `do_doctor` stub:

```python
def do_doctor(backend: Backend, args: list[str]) -> int:
    """Verify the meta-key invariants and the include graph.

    Output: progress and per-check status to stdout; failure details to stderr.
    Exit 0 if all clean; 1 if any check fails.
    """
    failures: list[str] = []

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

    # Check 2: recipients contain meta pubkey.
    if meta_key is not None and shutil.which("age-keygen") is not None:
        print("Checking .age-recipients ...", file=sys.stdout)
        with tempfile.NamedTemporaryFile(
            mode="w", prefix="secwrap-doctor-", suffix=".txt", delete=False
        ) as tf:
            keyfile = Path(tf.name)
            tf.write(meta_key.key.decode("utf-8"))
        try:
            result = subprocess.run(  # noqa: S603
                ["age-keygen", "-y", str(keyfile)],
                capture_output=True, text=True, check=False,
            )
            if result.returncode != 0:
                failures.append(f"age-keygen -y failed: {result.stderr.strip()}")
            else:
                meta_pubkey = result.stdout.strip()
                recipients_path = backend.store_dir / "config" / "env" / ".age-recipients"
                if not recipients_path.exists():
                    failures.append(".age-recipients missing")
                else:
                    recipients = recipients_path.read_text().splitlines()
                    if meta_pubkey not in [r.strip() for r in recipients]:
                        failures.append(
                            f"meta pubkey {meta_pubkey} not in .age-recipients"
                        )
                    else:
                        print("  OK", file=sys.stdout)
        finally:
            keyfile.unlink(missing_ok=True)

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
        try:
            for tool in backend.list_tools():
                resolve_includes(backend, tool, marker_loaded=set(), meta_key=meta_key)
            print("  OK", file=sys.stdout)
        except IncludeError as exc:
            failures.append(f"include graph: {exc}")

    if failures:
        print("\nDoctor found issues:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1

    print("\nAll checks passed.", file=sys.stdout)
    return 0
```

- [ ] **Step 4: Run tests; confirm all pass**

Run: `cd /home/wlritchi/.wlrenv && uv run --group dev pytest tests/test_secwrap.py -v`
Expected: 117 passed (112 prior + 5 new).

- [ ] **Step 5: Run autoformatter, pyright, ruff**

- [ ] **Step 6: Commit**

```bash
cd /home/wlritchi/.wlrenv && git add src/wlrenv/secwrap.py tests/test_secwrap.py && git commit -m "$(cat <<'EOF'
feat(secwrap): doctor subcommand for meta-key health checks

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: Update bash and xonsh aliases to include `--`

Defensive `--` for all auto-derived aliases. Two one-line changes.

**Files:**
- Modify: `env.bash`
- Modify: `xonshrc.py`

- [ ] **Step 1: Edit `env.bash:411`**

Change:
```bash
        alias "$cmd=secwrap $cmd"
```
to:
```bash
        alias "$cmd=secwrap -- $cmd"
```

- [ ] **Step 2: Edit `xonshrc.py:300`**

Change:
```python
                    XSH.aliases[cmd] = f'secwrap {cmd}'
```
to:
```python
                    XSH.aliases[cmd] = f'secwrap -- {cmd}'
```

- [ ] **Step 3: Verify shell init parses correctly**

Run: `cd /home/wlritchi/.wlrenv && bash -n env.bash && echo "bash syntax OK"`
Expected: `bash syntax OK`.

(xonsh has no equivalent syntax-only check; the file is normal Python and `pyright` will catch issues.)

Run: `cd /home/wlritchi/.wlrenv && uv run --group dev pyright xonshrc.py 2>&1 | head -20`
Expected: no new errors introduced (pre-existing errors elsewhere are not this task's concern).

- [ ] **Step 4: Commit**

```bash
cd /home/wlritchi/.wlrenv && git add env.bash xonshrc.py && git commit -m "$(cat <<'EOF'
chore(secwrap): pass -- to disable subcommand interpretation in shell aliases

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: Smoke test the installed entry point

Verifies the new entry point ships correctly. No live store entries on this machine, so all tests are shape/help/error-message checks.

**Files:** none modified.

- [ ] **Step 1: Reinstall**

Run: `uv tool install --reinstall /home/wlritchi/.wlrenv/ --python 3.12`
Expected: exit 0; output mentions `secwrap`.

- [ ] **Step 2: --help and --list still work**

Run: `~/.local/share/uv/tools/wlrenv/bin/secwrap --help`
Expected: USAGE printed; exit 0.

Run: `~/.local/share/uv/tools/wlrenv/bin/secwrap --list`
Expected: empty output; exit 0.

- [ ] **Step 3: `--` force-wrap still wraps when there's no entry**

Run: `~/.local/share/uv/tools/wlrenv/bin/secwrap -- bootstrap --version 2>&1 | head -5`
Expected: tries to exec `bootstrap` (probably "command not found" or whatever the host's `bootstrap` does — does NOT run `secwrap bootstrap`).

For a more reliable test: run a binary that definitely exists.
Run: `~/.local/share/uv/tools/wlrenv/bin/secwrap -- true && echo OK`
Expected: `OK` (exec'd `true`, exit 0).

- [ ] **Step 4: Subcommands reachable**

Run: `~/.local/share/uv/tools/wlrenv/bin/secwrap doctor 2>&1; echo "exit: $?"`
Expected: errors out (no meta, no live store entries on this machine), but reports it via the `doctor` flow — NOT a "command not found" or wrap-path error. The output should mention `config/env-meta` or `bootstrap`. Exit code: 1.

Run: `~/.local/share/uv/tools/wlrenv/bin/secwrap rotate-meta 2>&1`
Expected: prints the description (since no `--yes`); exit 0. OR if there's no meta entry, errors out with "no config/env-meta found"; exit 1. Either is acceptable — both confirm dispatch worked.

Run: `~/.local/share/uv/tools/wlrenv/bin/secwrap bootstrap 2>&1`
Expected: errors out about something specific to bootstrap (e.g., "age-keygen not found" if absent, or runs the bootstrap if everything's in place). Whatever the output, it's NOT the wrap path's "command not found" error.

- [ ] **Step 5: Marker short-circuit unchanged**

Run: `_SECWRAP_LOADED=true ~/.local/share/uv/tools/wlrenv/bin/secwrap true && echo OK`
Expected: `OK`.

- [ ] **Step 6: No commit (verification only)**

If any step shows a regression (e.g., `--list` broken, marker short-circuit not firing), STOP and diagnose.

---

## Self-review notes

**Spec coverage:**
- Use meta key during wrap path → Task 3. ✓
- `bootstrap` subcommand (passage flow) → Task 5. ✓
- `rotate-meta` subcommand → Task 6. ✓
- `doctor` subcommand → Task 7. ✓
- Meta-entry JSON schema (age) → Task 2 (`load_meta_key` validation). ✓
- Backend-mismatch hard error → Task 2 test. ✓
- Fallback warning when meta absent → Task 3 (decrypt_count >= 2 gate). ✓
- `--` interaction with subcommand dispatch → Task 1 (parser) + Task 4 (dispatch gate). ✓
- Pass-backend gating on subcommands → Task 4. ✓
- Recipient list manipulation → Task 5 (`_add_age_recipient`) + Task 6 (`_remove_age_recipient`). ✓
- Cycle detection in doctor → Task 7 (reuses `resolve_includes`). ✓

**Type consistency:** `MetaKey`, `MetaKeyError`, `load_meta_key`, `do_bootstrap`, `do_rotate_meta`, `do_doctor`, `_add_age_recipient`, `_remove_age_recipient`, `_run_or_fail` — names consistent across tasks. Subcommand handlers all return `int` and accept `(backend, args: list[str])`.

**Placeholder scan:** none — every step has concrete code, commands, or precise edits.

**Out-of-scope confirmation:** no pass-backend meta key (Phase 3); no per-include scoping; no caching beyond the marker.

**Test count progression:** 82 (Phase 2a end) → 86 → 93 → 99 → 104 → 109 → 112 → 117 (Phase 2b end). Total +35 tests.

**Risks I'm aware of:**
- Tests for `bootstrap` and `rotate-meta` mock `subprocess.run` heavily. If the real shell-out semantics drift (e.g., `passage insert` argument format changes), tests still pass but production fails. Mitigation: the smoke test (Task 9) runs the real binary against the local environment; any drift surfaces there.
- `MetaKey.decrypt` with `input=self.key` requires a `bytes` argument. Confirm `subprocess.run` handles `bytes` correctly when `text=False` (it does — it's the default contract).
- The `finally: meta_key = None` pattern is best-effort; Python's GC may or may not collect the bytes promptly. Acceptable for the documented threat model (defense in depth, not security boundary).
