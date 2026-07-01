# `secwrap` Phase 3 (pass backend gpg meta key) — Implementation Plan

**Goal:** Bring the `pass` (gpg) backend to parity with `passage`: include comments are
walked transitively, and an optional gpg meta key gives 1-prompt-per-outer-wrap decryption
(temp `$GNUPGHOME`, random passphrase, generate-with-passphrase bootstrap). Removes the
Phase 2 placeholder warning `secwrap: include comments are not yet implemented for the pass
backend; ignoring`. Ships `pass`-backend variants of `bootstrap`, `rotate-meta`, `doctor`.

**Spec reference:** `docs/specs/2026-05-07-secwrap-includes-design.md` — sections "Meta Entry"
(gpg schema), "Bootstrap → pass flow", "Rotation", "Backend specifics → gpg path", "Failure
Modes", "Security Considerations". Phase 3 is item 3 of "Implementation Sequencing".

**Predecessor state (after Phase 2b):** passage does full include walking + age meta key +
`bootstrap`/`rotate-meta`/`doctor`. `pass` short-circuits in `resolve_includes` (loads only the
root, warns on include comments) and its subcommands print "not yet supported ... Phase 3".

---

## Key gpg mechanics (decisions locked down)

These are the non-obvious points discovered by reading the installed `pass` (1.7.x) and gpg
behaviour. They drive the implementation and are the most likely source of bugs.

1. **`pass` encrypts without `--trust-model always`.** `GPG_OPTS` in the `pass` script is
   `( $PASSWORD_STORE_GPG_OPTS --quiet --yes --compress-algo=none --no-encrypt-to )` plus
   `--batch --use-agent`. It never adds `--trust-model always`. So a freshly-imported meta
   **public** key is untrusted, and `pass init` / `pass insert` (`gpg -e -r <fpr>`) will abort
   with "There is no assurance this key belongs to the named user". **Fix:** after importing
   the meta pubkey into the real keyring, set its ownertrust to ultimate via
   `gpg --import-ownertrust` fed `"<FPR>:6:\n"`. The meta key is our own key, so ultimate is
   appropriate; ownertrust only affects gpg's willingness to encrypt without prompting.

2. **Importing a passphrase-protected secret key needs no passphrase.** gpg imports the
   S2K-protected key material as-is; the passphrase is only required to *use* it. So the
   runtime `--import` step in `MetaKey` does NOT pass a passphrase. This is simpler and safer
   than the spec's sketch (which passed `--passphrase` to `--import`).

3. **Decryption needs the passphrase but not trust.** Trust gates encryption, not decryption.
   The temp `$GNUPGHOME` holds only the meta secret key (untrusted, fine). Each decrypt is
   `gpg --homedir H --batch --pinentry-mode loopback --passphrase-fd 0 --decrypt <path>` with
   the passphrase on stdin (fd 0) and the ciphertext supplied as a file argument.

4. **Passphrase never on argv.** Use `--passphrase-fd` (piped) everywhere, not `--passphrase
   <pp>` (which leaks to `ps`). This tightens the spec's argv-passphrase sketch.

5. **A temp `$GNUPGHOME` spawns its own gpg-agent.** Cleanup must
   `gpgconf --homedir H --kill gpg-agent` (best-effort) before `shutil.rmtree(H)`, else a stray
   agent lingers holding the tmpfs dir open.

6. **`config/env-meta` is encrypted only to the user identity.** It lives at
   `<store>/config/env-meta.gpg`, so `pass` resolves its recipients by walking *up* from
   `config/` — it never sees `config/env/.gpg-id`. Bootstrap adds the meta fingerprint only to
   `config/env/.gpg-id` (via `pass init -p config/env`), so the meta entry stays user-only
   automatically. No special-casing needed.

7. **Temp dir placement.** `mkdtemp(dir=…)` preferring `$XDG_RUNTIME_DIR` (tmpfs, per-user,
   0700), then `$TMPDIR`, then `/tmp`; `chmod 700` regardless. Matches the age path's intent
   and the spec's "gpg path" note.

---

## Plan decisions

1. **The include-graph walk is already backend-generic.** `resolve_includes`'s passage branch
   (cycle detection, sibling ordering, missing-include errors, per-entry decrypt via
   `backend.show`) works verbatim for `pass`. Phase 3 deletes the `pass` early-return and the
   `_PASS_INCLUDES_WARNING`; `pass` falls into the same walk. With no meta key it uses
   `backend.show` (= `pass show`, gpg-agent-cached) per entry; with a meta key it uses
   `MetaKey.decrypt`. This is Task 1 and is independently valuable (includes work on `pass`
   even before any meta key exists).

2. **`MetaKey` gains a gpg lifecycle without changing `resolve_includes`'s call site.**
   `MetaKey.decrypt(store_dir, entry, extension)` stays the interface. For gpg it lazily
   creates + imports into a temp `$GNUPGHOME` on first call, caches it, and reuses it for
   subsequent entries. A new `MetaKey.cleanup()` tears the homedir down (+ kills the agent).
   `main()` calls `cleanup()` in the `finally` that already nulls the reference.

3. **Frozen dataclass keeps a mutable cache cell.** Add `passphrase: bytes | None = None` and a
   private `_gpg: dict[str, Path] = field(default_factory=dict, compare=False, repr=False)`.
   Mutating the dict contents is allowed on a frozen instance (frozen blocks attribute
   rebinding, not object mutation). Positional construction `MetaKey("age", b"…")` still works.

4. **Signal safety.** The gpg temp homedir must not leak on SIGINT/SIGTERM. `main()` installs a
   handler for both that runs `meta_key.cleanup()` then re-raises the default disposition,
   registered only for the window where a live gpg homedir exists, and removed in `finally`.
   The normal + exception paths are already covered by the existing `try/finally`. (age needs
   none of this; the handler is a no-op when `_gpg` is empty.)

5. **`load_meta_key` parses the gpg schema.** For a `pass` backend the meta JSON must be
   `{"backend":"gpg","passphrase":"<b64>","key":"<armored>"}`. Missing `passphrase` or `key` is
   a hard `MetaKeyError`. The existing backend-mismatch check already yields the right message.

6. **Subcommands lose the passage-only gate.** `main()` currently prints "not yet supported for
   the pass backend (Phase 3)". Phase 3 removes that branch and dispatches to the same
   `do_bootstrap`/`do_rotate_meta`/`do_doctor`, which internally branch on `backend.name`.

7. **`do_*` internal split by backend.** Rather than duplicate the dispatchers, each `do_*`
   keeps its passage body and gains a `pass` body (helper `_*_pass`). Shared shell-out plumbing
   (`_run_or_fail`, JSON payload build) is reused. gpg-specific helpers
   (`_resolve_pass_gpg_ids`, `_gpg_generate_meta_key`, `_pass_init_recipients`) are new.

8. **`doctor --fix` for pass** repairs a missing inherited/meta recipient by rewriting
   `config/env/.gpg-id` to the correct set and running `pass init -p config/env`. No
   PQ/classic concern exists for gpg, so the pass doctor is structurally simpler than the age
   one (no meta-key-type rotation branch).

9. **All shell-outs mocked in unit tests.** `gpg`, `gpgconf`, `pass` are mocked via
   `mocker.patch("subprocess.run", side_effect=fake_run)`, mirroring the passage tests. One
   optional integration test may exercise a real `gpg` round-trip if `gpg` is on PATH
   (`@pytest.mark.skipif`).

---

## File structure

| Path                     | Action | Responsibility                                                                       |
|--------------------------|--------|--------------------------------------------------------------------------------------|
| `src/wlrenv/secwrap.py`  | Modify | Backend-generic walk; gpg `MetaKey` lifecycle + `cleanup`; `load_meta_key` gpg schema; `_resolve_pass_gpg_ids`, `_gpg_generate_meta_key`, `_pass_init_recipients`, `_kill_gpg_agent`; pass bodies for `do_bootstrap`/`do_rotate_meta`/`do_doctor`; `main()` cleanup + signal wiring + un-gate. |
| `tests/test_secwrap.py`  | Modify | Tests for pass include walking, gpg `MetaKey` decrypt/cleanup, gpg `load_meta_key`, pass bootstrap/rotate/doctor. |
| `docs/specs/2026-05-07-secwrap-includes-design.md` | Modify | Mark Phase 3 delivered in "Implementation Sequencing"; note the gpg mechanics deltas (ownertrust, import-without-passphrase). |

Ends ~+350 lines in `secwrap.py`. If the file crosses ~1000 lines it is a candidate for the
`secwrap/` package split flagged in Phase 2b, deferred again unless it actively hurts.

---

## Tasks

### Task 1 — Backend-generic include walk (removes the warning)

Delete the `if backend.name == "pass":` early-return block and `_PASS_INCLUDES_WARNING` in
`resolve_includes`. Update the docstring (drop the "pass short-circuits" paragraph). The
generic walk now serves both backends.

**Tests (append):**
- `test_resolve_includes_pass_walks_chain` — pass backend, `claude → docker`, `backend.show`
  mocked; asserts order `["docker","claude"]` and no warning on stderr.
- `test_resolve_includes_pass_cycle_raises` — pass backend cycle → `IncludeError`.
- `test_resolve_includes_pass_missing_dep_raises` — pass backend missing include → `IncludeError`.
- `test_main_pass_includes_no_warning` — `main(["claude"])` on pass with an include comment
  execs with merged env and emits no "not yet implemented" warning.
- Delete/replace any existing test asserting the pass warning fires.

Commit: `feat(secwrap): walk include graph on the pass backend`.

### Task 2 — gpg `MetaKey` lifecycle + `load_meta_key` gpg schema

Add `passphrase` + `_gpg` cache to `MetaKey`; implement the gpg branch of `decrypt`
(`_ensure_gpg_home` lazily mkdtemp+chmod700+import; per-entry `gpg --decrypt`), and
`cleanup()` (kill agent, rmtree). Extend `load_meta_key` to require `passphrase`+`key` for gpg.
Wire `main()`'s `finally` to call `cleanup()` and install/remove the SIGINT/SIGTERM handler.

**Tests (append):**
- `test_load_meta_key_valid_json_gpg` — pass backend, blob with backend/passphrase/key → fields set.
- `test_load_meta_key_gpg_missing_passphrase_raises`.
- `test_meta_key_gpg_decrypt_imports_once_and_decrypts` — mocked `subprocess.run`; assert one
  `--import`, then `--decrypt` per entry with `--pinentry-mode loopback` and passphrase on stdin.
- `test_meta_key_gpg_cleanup_kills_agent_and_removes_home` — after `cleanup()`, `gpgconf
  --kill gpg-agent` called and homedir gone.
- `test_main_pass_uses_gpg_meta_key` — end-to-end with mocked gpg: single import, silent
  per-include decrypt, merged env, `_SECWRAP_LOADED` set, `cleanup()` invoked before exec.

Commit: `feat(secwrap): gpg meta key (temp GNUPGHOME) for in-process pass decryption`.

### Task 3 — `do_bootstrap` pass body

Helpers: `_resolve_pass_gpg_ids(store_dir)` (walk `config/env/.gpg-id` → `config/.gpg-id` →
`.gpg-id`), `_gpg_generate_meta_key()` (temp homedir, random 32-byte b64 passphrase,
`--quick-generate-key … secwrap-meta default default 0` via loopback; return
`(fingerprint, armored_secret, armored_pub, passphrase)`), `_pass_init_recipients(store_dir,
subpath, ids)`. Bootstrap: pre-flight (`gpg`, `pass` on PATH; meta absent; base ids resolvable);
generate; import pubkey + set ultimate ownertrust; `pass init -p config/env <base…> <meta_fp>`;
`pass insert -m config/env-meta` with the JSON payload; cleanup temp homedir.

**Tests (append):** happy path (mocked gpg/pass; asserts ownertrust set, `pass init` includes
both ids, meta payload has backend=gpg + passphrase + key); meta-already-exists → exit 1;
`gpg` missing → exit 1; no resolvable base ids → exit 1; `pass init` failure aborts + cleans up.

Commit: `feat(secwrap): bootstrap subcommand for pass gpg meta key`.

### Task 4 — `do_rotate_meta` + `do_doctor` pass bodies

`rotate-meta` (pass): require `--yes`; parse existing meta; derive old fp; generate new key;
import new pubkey + ownertrust; rewrite `config/env/.gpg-id` swapping old→new meta fp; `pass
init -p config/env`; replace `config/env-meta`; cleanup.
`doctor` (pass): meta parses (gpg schema); `config/env/.gpg-id` contains meta fp + inherited
ids (`--fix` rewrites + `pass init`); every entry decrypts under the meta key; include graph
well-formed. Reuse the shared report/stderr structure from the passage doctor.

**Tests (append):** rotate without `--yes` describes + exit 0; rotate happy path swaps fp and
re-inits; doctor clean → exit 0; doctor missing meta fp → exit 1 + `--fix` repairs; doctor
detects an undecryptable entry.

Commit: `feat(secwrap): rotate-meta and doctor for the pass gpg backend`.

### Task 5 — Un-gate `main()` dispatch + finalize

Remove the `backend.name != "passage"` subcommand gate. Update `main()`/module docstrings to
drop Phase-3-pending language. Update the design doc's sequencing section. Full `pytest` +
`ruff format` + `ruff check` + `pyright` clean. Merge the worktree back to `main`.

Commit: `feat(secwrap): enable bootstrap/rotate-meta/doctor on the pass backend`.

---

## Failure modes (additions to the spec table, gpg path)

| Condition                                   | Behaviour                                                                       |
|---------------------------------------------|---------------------------------------------------------------------------------|
| Meta pubkey imported but untrusted          | Bootstrap sets ultimate ownertrust; without it `pass init` aborts (guarded).    |
| Temp `$GNUPGHOME` unwritable (all roots)    | Hard error before generate/import; nothing partially written.                   |
| gpg-agent survives cleanup                  | `gpgconf --kill` is best-effort; rmtree still runs; a leaked agent is inert.     |
| Meta entry present but `passphrase` missing | Hard `MetaKeyError` from `load_meta_key`; wrap path exits 1, does not exec.      |

## Out of scope

- Migrating an existing `pass` store's layout; users run `secwrap bootstrap` explicitly.
- Windows/macOS-specific temp-dir quirks beyond the `XDG_RUNTIME_DIR`/`TMPDIR`/`/tmp` ladder.
- The `secwrap/` package split (still deferred).
</content>
</invoke>
