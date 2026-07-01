# `secwrap` Includes — Design

## Goal

Extend `secwrap` so a `config/env/<tool>` entry can declare other entries it depends on. When a top-level wrap (e.g. `secwrap claude`) loads its env, includes are resolved transitively and merged. A nested `secwrap pnpm` invocation (typical in non-interactive bash spawned by Claude) detects that pnpm is already loaded via an env marker and skips decryption entirely.

Today: each `config/env/<tool>` entry produces one decryption per invocation. A Claude session that runs `pnpm install` internally — and then `direnv … docker compose …` from within pnpm — incurs a fresh TouchID/Yubikey prompt for every wrapped binary, because shell aliases do not expand in non-interactive bash. There is no way to compose secrets across tools, and no way to skip redundant decryption when the secrets are already in env.

Two friction points motivate this:
- **(B) TouchID-per-invocation in non-interactive bash:** the immediate ergonomic pain.
- **(A) Tools that bypass `secwrap` entirely:** e.g. `pnpm` tasks that hand off to `direnv ... docker compose ...`. Pre-loading docker's env at the outer wrap puts those vars in scope for the bypassing call.

## Approach

Three pieces, each independently useful:

1. **Include comments.** A `# secwrap-include: <tool> [<tool>...]` line in any `config/env/<tool>` entry declares dependencies. secwrap walks the graph and merges their `KEY=VALUE` env contributions.
2. **Loaded marker.** secwrap exports `_SECWRAP_LOADED=<tool>:<tool>:...` (colon-separated, alphabetized) before exec. On every invocation, secwrap checks whether the target entry already appears in the marker; if so, exec immediately, no decryption.
3. **Optional meta key.** A separate pass/passage entry holds an age (or gpg) private key, encrypted only to the user's identity. All `config/env/*` entries are encrypted to *both* the user identity and the meta key's public half. On the first decryption of a wrap, secwrap unlocks the meta entry once (1 TouchID), holds the key in process memory, and uses it to decrypt every include-graph entry in-process — no further prompts.

Pieces 1 and 2 are independent. Piece 3 is opt-in: if no meta entry exists, secwrap falls back to plain per-entry decryption (1 prompt per include), which on home machines with `gpg-agent` caching is generally tolerable.

## Data Model

### Include Comments

In any `config/env/<tool>` entry, a line of the form:

```
# secwrap-include: pnpm docker
```

declares includes. Whitespace-separated tool names; multiple `secwrap-include:` lines OR'd together. Tool names match `[A-Za-z0-9._-]+` and resolve to `config/env/<name>` entries.

The comment may appear anywhere in the file; secwrap scans the full plaintext after decryption. (Restricting to a leading comment block buys nothing — the file is already decrypted by the time we look.)

**Cycle detection** via a visited set during traversal. A cycle is a hard error: `secwrap: cycle detected: claude → pnpm → claude`.

**Conflict resolution:** includes load first, in topological order (deepest dependency first). The directly-invoked entry loads last, so its `KEY=VALUE` lines win on duplicates. Among siblings at the same depth, alphabetical order by entry name — purely for determinism.

### Loaded Marker

```
_SECWRAP_LOADED=<tool>[:<tool>]*
```

Colon-separated, alphabetized, deduplicated. Example: `_SECWRAP_LOADED=claude:docker:pnpm`.

On invocation, before any decryption, secwrap parses the current value (if set) and checks whether the *secret entry name* (after `--from` resolution) appears in it. If yes → `exec` directly, no work. If no → proceed with full decryption + include traversal, then update the marker to the alphabetized union of (existing marker) ∪ (newly loaded set).

The marker is not a secret: it's a list of tool names. It's exported deliberately so child processes inherit it.

`--from` interaction: the marker tracks which *secrets* are loaded, keyed by entry name. `secwrap --from claude bar` records `claude` (because claude's secrets and includes are now in env), not `bar`. So a subsequent `secwrap claude qux` short-circuits, but `secwrap bar` (a different entry) does not.

### Meta Entry

**Path:** `config/env-meta` — a sibling of the `config/env/` directory, deliberately *outside* it so `--list` does not surface it.

**Format:** a single JSON object. Backend-specific schema:

**age (passage):**
```json
{
  "backend": "age",
  "key": "AGE-SECRET-KEY-1..."
}
```

**gpg (pass):**
```json
{
  "backend": "gpg",
  "passphrase": "<base64-encoded random bytes>",
  "key": "-----BEGIN PGP PRIVATE KEY BLOCK-----\n..."
}
```

The `backend` field is validated against the runtime-detected backend; mismatch is a hard error.

The gpg meta key is passphrase-protected with a per-setup random 32-byte passphrase generated from `/dev/urandom`. The passphrase lives in `config/env-meta` (alongside the key it protects) and in secwrap's process memory at runtime. It is *not* a security boundary against same-user attackers — both the passphrase and the encrypted-at-rest key are protected by the same user identity. It exists so the on-disk form of the meta key in the temp `$GNUPGHOME` is non-trivial to lift (a `inotify` race against the brief unprotected window discussed during design).

### Recipient Configuration

**passage:** `$PASSAGE_DIR/config/env/.age-recipients` lists user pub key + meta pub key. New entries inserted under `config/env/` automatically inherit. Re-encryption after the meta key is added (or rotated) via `passage reencrypt config/env`.

**pass:** `$PASSWORD_STORE_DIR/config/env/.gpg-id` lists user fingerprint + meta fingerprint. `pass init -p config/env <user-fingerprint> <meta-fingerprint>` re-encrypts.

The meta entry itself is encrypted *only* to the user identity (never to itself), so its decryption always requires the real identity.

## Runtime Flow

### Outer wrap (target not yet in `_SECWRAP_LOADED`)

```
secwrap claude
├── parse args, resolve secret_key (default: claude; --from overrides)
├── if secret_key ∈ _SECWRAP_LOADED: exec immediately (see "Nested" below)
├── try to load meta entry:
│   ├── decrypt config/env-meta via real identity (1 prompt) → JSON blob
│   ├── if missing: meta_key=None (fallback to per-entry prompts)
│   └── if present: extract backend identity, hold in shell vars
├── walk include graph from secret_key:
│   ├── topological order, conflict-resolved (direct entry wins)
│   ├── for each entry:
│   │   ├── if meta_key: decrypt directly via age/gpg with meta_key (no prompt)
│   │   └── else: invoke `passage show` / `pass show` (1 prompt)
│   └── parse KEY=VALUE lines, merge
├── export merged vars
├── update _SECWRAP_LOADED to alphabetized union
├── cleanup_meta() (zero passphrase var, rm -rf gpg tmpdir)
└── exec tool_name "$@"
```

### Nested wrap (target already in `_SECWRAP_LOADED`)

```
secwrap pnpm        (inside claude, _SECWRAP_LOADED=claude:docker:pnpm)
├── parse args
├── pnpm ∈ _SECWRAP_LOADED → skip everything
└── exec pnpm "$@"
```

Zero decryption, zero filesystem access, zero prompts.

### Backend specifics

**age path:**
- Meta key bytes piped to `age -d --identity /dev/stdin "$store_dir/config/env/<tool>.age"`. No on-disk persistence.

**gpg path:**
- `tmp=$(mktemp -d "${XDG_RUNTIME_DIR:-${TMPDIR:-/tmp}}/secwrap.XXXXXX"); chmod 700 "$tmp"`. Prefer `XDG_RUNTIME_DIR` (Linux/systemd, tmpfs, per-user, mode 0700). Fall back to `TMPDIR` (macOS sets a per-user `/var/folders/...` path mode 0700 by default), then `/tmp` as last resort. The `chmod 700` ensures other-user readability is closed regardless of which root we landed on.
- `GNUPGHOME=$tmp gpg --batch --pinentry-mode loopback --passphrase "$passphrase" --import <<< "$key"`. Imports the meta key in already-passphrase-protected form (so the on-disk file in `$tmp/private-keys-v1.d/` never exists in unprotected form at runtime).
- For each include: `GNUPGHOME=$tmp gpg --batch --pinentry-mode loopback --passphrase "$passphrase" --decrypt "$store_dir/config/env/<tool>.gpg"`.
- Cleanup: `rm -rf "$tmp"; unset passphrase` immediately before `exec`. A `trap` on `EXIT/INT/TERM` runs the same cleanup if secwrap exits before reaching exec (e.g. include resolution failure).

## Bootstrap

A `secwrap bootstrap` subcommand wraps both flows, dispatching by detected backend.

### passage flow

1. `age-keygen -pq -o /tmp/secwrap-meta.txt` (`-pq` selects the post-quantum-secure key type when available, falling back to X25519 otherwise).
2. `pubkey=$(age-keygen -y /tmp/secwrap-meta.txt)`
3. Append `pubkey` to `$PASSAGE_DIR/config/env/.age-recipients` (creating the file if absent — it should already include the user pub key).
4. `passage reencrypt config/env`
5. `jq -n --arg k "$(cat /tmp/secwrap-meta.txt)" '{backend: "age", key: $k}' | passage insert -m config/env-meta`
6. `shred -u /tmp/secwrap-meta.txt`

### pass flow

1. `tmp_homedir=$(mktemp -d); chmod 700 "$tmp_homedir"`
2. `passphrase=$(head -c 32 /dev/urandom | base64)`
3. `GNUPGHOME=$tmp_homedir gpg --batch --quick-generate-key --pinentry-mode loopback --passphrase "$passphrase" "secwrap-meta" default default 0` — the key is passphrase-protected on disk from the moment it exists.
4. `fingerprint=$(GNUPGHOME=$tmp_homedir gpg --list-secret-keys --with-colons | awk -F: '/^fingerprint:/ { print $10; exit }')`
5. `key=$(GNUPGHOME=$tmp_homedir gpg --batch --pinentry-mode loopback --passphrase "$passphrase" --export-secret-keys --armor "$fingerprint")`
6. `pubkey=$(GNUPGHOME=$tmp_homedir gpg --export --armor "$fingerprint")`
7. Import `pubkey` into the real keyring: `gpg --import <<< "$pubkey"` (so the recipient list resolves locally).
8. Append `fingerprint` to `$PASSWORD_STORE_DIR/config/env/.gpg-id`.
9. `pass init -p config/env "$user_fingerprint" "$fingerprint"` to re-encrypt the subtree.
10. `jq -n --arg p "$passphrase" --arg k "$key" '{backend: "gpg", passphrase: $p, key: $k}' | pass insert -m config/env-meta`
11. `rm -rf "$tmp_homedir"; unset passphrase key pubkey`

The user-id is just `secwrap-meta` (no hostname): the meta key is shared across all machines that share the password store. The key never exists on disk in unprotected form — `--quick-generate-key --passphrase X` writes the S2K-protected form directly. The runtime `--import` window cannot be closed the same way (see Security Considerations); generation can.

## Rotation

### Meta key rotation

`secwrap rotate-meta`:

1. Generate a new meta key (same procedure as bootstrap, into a temp homedir / file).
2. Update the recipient list (`.age-recipients` / `.gpg-id`) — replace old meta pub with new.
3. Re-encrypt all `config/env/*` entries: `passage reencrypt config/env` / `pass init -p config/env ...`.
4. Replace `config/env-meta` with the new wrapped key + (gpg) new passphrase.

### Per-entry secret rotation

Unchanged from today: edit the entry. As long as recipients haven't changed, no extra step. If recipients drifted (e.g. a new entry was inserted before the meta key was added), `passage reencrypt` / `pass init` fixes it.

### Drift detection

`secwrap doctor` (new subcommand) checks:
- `config/env-meta` exists and parses as the expected JSON schema for the configured backend.
- Recipient files (`.age-recipients` / `.gpg-id`) include the meta pub key / fingerprint.
- Every `config/env/*` entry decrypts under the meta key (i.e., was re-encrypted after the meta key was added).
- The include graph has no cycles, and every referenced include resolves to an existing entry.

## Failure Modes

| Condition                                          | Behavior                                                                                                          |
|----------------------------------------------------|-------------------------------------------------------------------------------------------------------------------|
| Meta entry missing                                 | Fall back to per-entry decryption. Stderr (once per outer wrap): `secwrap: meta key absent; N includes will require N prompts` |
| Meta entry malformed JSON                          | Hard error: `secwrap: config/env-meta is not valid JSON`                                                          |
| Meta entry backend mismatch                        | Hard error: `secwrap: meta entry declares backend=X but detected backend is Y`                                    |
| Include comment references missing entry           | Hard error: `secwrap: claude includes 'pnpm' but config/env/pnpm not found`                                       |
| Include cycle                                      | Hard error: `secwrap: cycle detected: claude → pnpm → docker → claude`                                            |
| Entry not encrypted to meta key                    | Falls back to user-identity decryption *for that entry only* (1 prompt). Stderr: `secwrap: config/env/foo not encrypted to meta key; will prompt`. Suggest running `secwrap doctor` / `passage reencrypt`. |
| Marker malformed (e.g. user manually set it)       | Treat as empty; full decryption path. No warning (the user did this on purpose, presumably).                      |
| All of `$XDG_RUNTIME_DIR`, `$TMPDIR`, `/tmp` unwritable (gpg path) | Hard error: `secwrap: cannot create temp GNUPGHOME (no writable runtime/temp dir found)`.                          |

## Security Considerations

- **Meta key at rest:** encrypted only to the user's identity. Same protection as any single `config/env/*` entry today.
- **Meta key in memory:** lives in shell variables in secwrap's process and (gpg path) in a temp `$GNUPGHOME` keyring at mode 0700. Both released before `exec`.
- **Random passphrase (gpg path):** also in shell variables, also released before `exec`. Not a same-user-attacker boundary; it's defense in depth so the on-disk form of the meta key in `$tmp` is always passphrase-protected.
- **Marker is non-secret:** it's a list of tool names. Exporting it to children is intentional.
- **Confused-deputy avoided:** the meta key is *not* propagated through env to children. A wrapped tool (e.g. claude) cannot use the meta key to decrypt entries outside its declared include set. Each top-level wrap re-decrypts the meta entry.
- **No bootstrap window:** `gpg --quick-generate-key --passphrase X` writes the protected form directly, so the meta key never exists in unprotected form on disk. The asymmetry with runtime `--import` (which has no atomic "set passphrase" form) is unavoidable through the CLI; setting it via `gpg-connect-agent` Assuan-protocol commands is brittle, undocumented surface for a marginal delta.
- **Plaintext env secrets:** unchanged from today. Wrapped tools see their secrets as env vars; subprocesses inherit them. The include feature *broadens* what a wrapped tool sees (deliberately), so authors of `secwrap-include` declarations should treat them as scope grants.

## Implementation Language

Reimplement secwrap in Python, matching the existing `bin/shims/` pattern (`uv run -qs` shebang with inline `# /// script` dependency declarations — see `bin/shims/claude` for precedent). The current bash implementation is workable for the existing scope but doesn't fit cleanly with the new design's graph traversal, JSON parsing, multi-subcommand dispatcher, and the `try/finally`-style cleanup discipline the gpg meta-key path needs. Python keeps the "single file, no build step, Nix-distributable" properties of the current bash form while giving us real exception handling, dataclasses for the entry/graph types, and `subprocess` for the age/gpg/pass shell-outs.

Distribution stays through the existing `machines/pkgs/secwrap.nix` derivation; only the inner script changes from `writeShellScriptBin` to a Python script (likely via `pkgs.writers.writePython3Bin` or a thin wrapper that ensures `uv` / Python is on PATH).

### Backend selection

The current bash implementation bakes the backend into the script at Nix-eval time via the `backend ? "pass"` derivation parameter. The Python rewrite drops this and selects the backend at runtime:

1. If `$SECWRAP_BACKEND` is set, use it (`pass` or `passage`); error on any other value.
2. Otherwise auto-detect: if the `passage` binary is on PATH and `$PASSAGE_DIR` (or default `~/.passage/store`) exists, use passage; else if `pass` is on PATH and `$PASSWORD_STORE_DIR` (or default `~/.password-store`) exists, use pass; else hard error.

Realistically each machine has exactly one of pass or passage configured, so auto-detect is unambiguous in practice. The env var is the override for the testing/edge cases. The Nix layer collapses to "place the file"; the `backend` derivation parameter is removed.

The rewrite is *not* a refactor opportunity — Phase 1 is functional parity with the current bash, no new behavior. New behavior arrives only in Phase 2.

## Implementation Sequencing

1. **Phase 1: Python rewrite.** Functional parity with the current bash `secwrap`: argument parsing (`--from`, `--list`, `--help`), `config/env/<tool>` lookup, KEY=VALUE parsing, `os.execvp`. No new behavior. Old bash version is removed in the same change. This phase exists to give Phases 2–3 a sane substrate.
2. **Phase 2: passage backend includes + marker + meta key.** Includes (comment scanning, transitive resolution, cycle detection, conflict resolution), `_SECWRAP_LOADED` marker, optional age meta key. The pass code path emits a stderr warning when an include comment is encountered: `secwrap: include comments are not yet implemented for the pass backend; ignoring`. The marker is backend-independent and works for pass too in this phase.
3. **Phase 3: pass backend gpg meta key.** *(Delivered — see `docs/plans/2026-05-07-secwrap-includes-phase-3-impl.md`.)* Temp `$GNUPGHOME`, random passphrase, generate-with-passphrase bootstrap. The pass backend now walks the include graph like passage; the Phase 2 warning is removed. Two deltas from the sketch above, discovered against real gpg/`pass`:
   - **Ownertrust, not `--trust-model always`:** `pass` invokes `gpg -e` without a trust override, so after importing the meta *public* key into the real keyring, bootstrap/rotate/doctor set its ownertrust to ultimate (`gpg --import-ownertrust` fed `<FINGERPRINT>:6:`) so `pass init`/`insert` will encrypt to it non-interactively.
   - **Import needs no passphrase; decrypt does:** the runtime `--import` of the passphrase-protected secret key runs without a passphrase (gpg imports the S2K-protected form as-is); the passphrase is supplied only at decrypt time via `--passphrase-fd` (never argv). A temp `$GNUPGHOME` spawns its own gpg-agent, killed via `gpgconf --kill gpg-agent` before the homedir is removed.

`secwrap bootstrap`, `secwrap rotate-meta`, and `secwrap doctor` arrive in Phase 2 (passage variants) and gained pass-backend implementations in Phase 3.

The data model (this document) is finalized for Phases 2–3 from the start; Phase 1 doesn't touch it.

## Out of Scope

- Caching across `secwrap` invocations beyond the marker (e.g. a persistent decrypted-blob cache on tmpfs).
- Any change to `--list` semantics (still returns leaf tools under `config/env/`).
- Replacing pass/passage with a different backend.
- Per-include scoping of the meta key (would require multiple meta keys; complexity not justified by the current threat model — the same-user attacker who could exploit a too-broad meta key can already read env vars and `ptrace` the process).
