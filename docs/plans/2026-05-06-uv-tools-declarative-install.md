# Declarative `uv tool install` — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Nix-driven declarative mechanism for `uv tool install`, then migrate `hyfetch` from the existing `pyproject.toml` re-export hack to the new mechanism as a working test case.

**Architecture:** Home-manager option `home.uvTools` (attrset of submodules) → JSON manifest at `~/.config/wlrenv/uv-tools.json` → bash installer iterates the manifest and runs `uv tool install` per entry → installer wired into `hooks/post-upgrade` after `wlr-nix-rebuild`. See `docs/specs/2026-05-06-uv-tools-declarative-install.md` for full design rationale.

**Tech Stack:** Nix (home-manager modules), bash, `jq`, `uv`.

**Note on TDD:** The artifacts here are Nix modules and a bash installer with no natural unit-test harness in this repo. Each task verifies by running the actual tools and inspecting state (manifest contents, `uv tool list`, `which hyfetch`). Where there's no good way to "watch the test fail first," the equivalent is "observe the current state, make the change, observe the new state."

**Branch:** Work on `feat/uv-tools-declarative` off `main`. Commit per task.

---

### Task 0: Create feature branch

**Files:** none.

- [ ] **Step 1: Create and check out branch**

```bash
cd /home/wlritchi/.wlrenv
git checkout -b feat/uv-tools-declarative
```

- [ ] **Step 2: Verify clean starting state**

```bash
git status
```

Expected: `On branch feat/uv-tools-declarative`, `nothing to commit, working tree clean`.

---

### Task 1: Scaffold `machines/uv-tools.nix` with option + renderer

Create the option definition and manifest-rendering logic, but leave the public list empty. Confirms the schema compiles and the manifest path works before adding any tool entries.

**Files:**
- Create: `machines/uv-tools.nix`
- Modify: `machines/common.nix` (add to `imports`)

- [ ] **Step 1: Create `machines/uv-tools.nix`**

Write the following file:

```nix
{
  config,
  lib,
  pkgs,
  ...
}:

let
  currentPlatform = if pkgs.stdenv.isDarwin then "darwin" else "linux";

  toolType = lib.types.submodule {
    options = {
      spec = lib.mkOption {
        type = lib.types.str;
        description = "Argument passed to `uv tool install` (PyPI spec, git+URL, or path).";
      };
      python = lib.mkOption {
        type = lib.types.nullOr lib.types.str;
        default = null;
        description = "Python version pin, passed as `--python` if non-null.";
      };
      withDeps = lib.mkOption {
        type = lib.types.listOf lib.types.str;
        default = [ ];
        description = "Extra deps for the tool's environment, each passed as `--with`.";
      };
      platforms = lib.mkOption {
        type = lib.types.listOf (lib.types.enum [ "linux" "darwin" ]);
        default = [ "linux" "darwin" ];
        description = "Platforms on which to install this tool.";
      };
      disabled = lib.mkOption {
        type = lib.types.bool;
        default = false;
        description = "If true, exclude this tool from the manifest. Overlay-friendly suppression.";
      };
    };
  };

  resolved = lib.filterAttrs (
    _name: tool: !tool.disabled && lib.elem currentPlatform tool.platforms
  ) config.home.uvTools;

  manifest = pkgs.writeText "wlrenv-uv-tools.json" (builtins.toJSON resolved);
in
{
  options.home.uvTools = lib.mkOption {
    type = lib.types.attrsOf toolType;
    default = { };
    description = "Python CLIs to install via `uv tool install`. Keyed by tool name.";
  };

  config = {
    home.uvTools = { };

    home.file.".config/wlrenv/uv-tools.json".source = manifest;
  };
}
```

- [ ] **Step 2: Import from `common.nix`**

In `machines/common.nix`, the file currently has no `imports`. Add an `imports` section. The minimal change is to add the line at the top of the body:

```nix
{
  imports = [ ./uv-tools.nix ];

  options.custom.krewPlugins = lib.mkOption {
    # ... existing content
```

(Insert before the existing `options.custom.krewPlugins` block. Do not modify any existing content.)

- [ ] **Step 3: Rebuild and verify manifest exists**

```bash
wlr-nix-rebuild
ls -la ~/.config/wlrenv/uv-tools.json
cat ~/.config/wlrenv/uv-tools.json
```

Expected: `~/.config/wlrenv/uv-tools.json` is a symlink into the nix store; `cat` prints `{}`.

- [ ] **Step 4: Commit**

```bash
git add machines/uv-tools.nix machines/common.nix
git commit -m "feat(nix): add home.uvTools option and manifest renderer

Empty schema scaffold for declarative \`uv tool install\`. Renders to
~/.config/wlrenv/uv-tools.json after platform gating and disabled-flag
filtering. Public list is empty in this commit; entries follow."
```

---

### Task 2: Add `hyfetch` to the public list

Populate the option with one entry to validate the rendering path and provide the migration target.

**Files:**
- Modify: `machines/uv-tools.nix` (add to `home.uvTools`)

- [ ] **Step 1: Add hyfetch to `home.uvTools`**

In `machines/uv-tools.nix`, change:

```nix
    home.uvTools = { };
```

to:

```nix
    home.uvTools = {
      hyfetch = {
        spec = "hyfetch>=2.0.0,<3.0.0";
      };
    };
```

- [ ] **Step 2: Rebuild and verify manifest content**

```bash
wlr-nix-rebuild
cat ~/.config/wlrenv/uv-tools.json | jq .
```

Expected: a JSON object with one key `hyfetch`, all five fields present (`spec`, `python: null`, `withDeps: []`, `platforms: ["linux", "darwin"]`, `disabled: false`).

- [ ] **Step 3: Commit**

```bash
git add machines/uv-tools.nix
git commit -m "feat(nix): declare hyfetch in home.uvTools

First entry in the new declarative tool list. Not yet installed via the
new mechanism — the wlrenv tool's pyproject.toml still owns hyfetch's
PATH entry until task 5 of the migration plan."
```

---

### Task 3: Implement `bin/meta/wlr-uv-tools-install`

Bash installer that parses the manifest with `jq` and runs `uv tool install` per entry. Failures on individual tools log via `wlr-err` but do not abort.

**Files:**
- Create: `bin/meta/wlr-uv-tools-install`

- [ ] **Step 1: Write the script**

```bash
#!/usr/bin/env bash
# Install Python CLIs declared in ~/.config/wlrenv/uv-tools.json via `uv tool install`.
# Manifest is rendered by machines/uv-tools.nix during home-manager activation.
# Dependencies: uv, jq.
set -euo pipefail

manifest="$HOME/.config/wlrenv/uv-tools.json"

if ! command -v uv >/dev/null 2>&1; then
    wlr-err "uv not installed (expected via nix-rebuild)"
    exit 1
fi

if ! command -v jq >/dev/null 2>&1; then
    wlr-err "jq not installed (expected via nix-rebuild)"
    exit 1
fi

if [ ! -f "$manifest" ]; then
    wlr-warn "manifest not found at $manifest; run wlr-nix-rebuild first"
    exit 0
fi

# Iterate keys; for each, build a uv tool install command from the entry's fields.
mapfile -t names < <(jq -r 'keys[]' "$manifest")

if [ "${#names[@]}" -eq 0 ]; then
    exit 0
fi

failures=()
for name in "${names[@]}"; do
    spec=$(jq -r --arg n "$name" '.[$n].spec' "$manifest")
    python=$(jq -r --arg n "$name" '.[$n].python // empty' "$manifest")

    args=(uv tool install --quiet)
    if [ -n "$python" ]; then
        args+=(--python "$python")
    fi

    while IFS= read -r dep; do
        [ -n "$dep" ] && args+=(--with "$dep")
    done < <(jq -r --arg n "$name" '.[$n].withDeps[]?' "$manifest")

    args+=("$spec")

    wlr-working "uv-tool: $name"
    if ! "${args[@]}"; then
        failures+=("$name")
    fi
done

if [ "${#failures[@]}" -gt 0 ]; then
    wlr-err "failed to install: ${failures[*]}"
    exit 1
fi
```

- [ ] **Step 2: Make executable**

```bash
chmod +x /home/wlritchi/.wlrenv/bin/meta/wlr-uv-tools-install
```

- [ ] **Step 3: Smoke-test against current manifest**

```bash
wlr-uv-tools-install
```

Expected: prints `uv-tool: hyfetch`, then either succeeds or fails with a clear message about a script-name conflict (the wlrenv tool currently owns `~/.local/bin/hyfetch`). Either outcome is acceptable for this commit — the migration in task 5 resolves the conflict.

If it fails with the conflict, the error message is the relevant signal. If it succeeds (e.g. uv decided to overwrite, or the wlrenv tool wasn't installed), check `uv tool list` for `hyfetch` as a separate entry.

- [ ] **Step 4: Commit**

```bash
git add bin/meta/wlr-uv-tools-install
git commit -m "feat(meta): add wlr-uv-tools-install

Reads ~/.config/wlrenv/uv-tools.json and runs \`uv tool install\` per
entry. Logs failures and continues. Not yet wired into the post-upgrade
hook."
```

---

### Task 4: Wire installer into `hooks/post-upgrade`

Run the installer automatically after `wlr-nix-rebuild` in the post-upgrade flow, so `wlr-check-update` keeps the declarative tool list in sync.

**Files:**
- Modify: `hooks/post-upgrade`

- [ ] **Step 1: Append installer call**

The current end of `hooks/post-upgrade` is:

```bash
# Rebuild Nix environment (includes niri-spacer and other Rust utilities)
"$WLR_ENV_PATH/bin/meta/wlr-nix-rebuild"
```

Change to:

```bash
# Rebuild Nix environment (includes niri-spacer and other Rust utilities)
"$WLR_ENV_PATH/bin/meta/wlr-nix-rebuild"

# Install/update declarative uv tools (manifest rendered by wlr-nix-rebuild)
"$WLR_ENV_PATH/bin/meta/wlr-uv-tools-install"
```

- [ ] **Step 2: Verify hook runs end-to-end**

```bash
"$WLR_ENV_PATH/hooks/post-upgrade"
```

Expected: dotfile sync runs, gitconfig checks pass, nix-rebuild runs, then installer runs. Whole script exits 0 even if installer reports a conflict (because installer's exit 1 will fail the hook — that's intentional, surfacing real failures).

If the installer fails on the hyfetch conflict, that's expected and resolves in Task 5. To proceed past task 4 with a clean commit, either:
- Skip running the hook end-to-end here (ok — the wiring is the change; test it after task 5).
- Or run task 5 before re-running the hook.

The plan defers the end-to-end hook test to task 6.

- [ ] **Step 3: Commit**

```bash
git add hooks/post-upgrade
git commit -m "feat(hooks): run wlr-uv-tools-install after nix-rebuild

Declarative uv tools now sync as part of wlr-check-update."
```

---

### Task 5: Migrate `hyfetch` out of `pyproject.toml`

Remove the dependency and the script re-export, refresh the wlrenv tool venv to drop the old `~/.local/bin/hyfetch` symlink, then run the new installer to bring hyfetch back as a standalone tool.

**Files:**
- Modify: `pyproject.toml` (remove two lines)

- [ ] **Step 1: Remove hyfetch from `[project.dependencies]`**

In `pyproject.toml`, remove the line:

```
    "hyfetch>=2.0.0,<3.0.0",
```

Delete the line entirely (do not leave a blank or a comment).

- [ ] **Step 2: Remove hyfetch from `[project.scripts]`**

In `pyproject.toml`, remove the line:

```
hyfetch = 'hyfetch.__main__:run_rust'
```

- [ ] **Step 3: Refresh wlrenv tool venv to drop the script**

```bash
uv tool install --quiet "$WLR_ENV_PATH/" --python "$WLR_PYTHON_VERSION" --reinstall
```

`--reinstall` ensures the wlrenv tool's bin directory is regenerated, removing `~/.local/bin/hyfetch`.

Verify:

```bash
ls -la ~/.local/bin/hyfetch 2>&1 || echo "hyfetch script gone (expected)"
```

Expected: either "No such file or directory" or the file does not exist.

- [ ] **Step 4: Run the installer to bring hyfetch back as standalone**

```bash
wlr-uv-tools-install
```

Expected: prints `uv-tool: hyfetch`, exits 0.

Verify:

```bash
which hyfetch
uv tool list | grep hyfetch
hyfetch --help | head -1
```

Expected: `which hyfetch` returns `~/.local/bin/hyfetch`; `uv tool list` shows `hyfetch v<version>` as its own entry; `--help` prints something sensible (does not error).

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml
git commit -m "refactor(deps): migrate hyfetch from pyproject re-export to home.uvTools

hyfetch was previously installed as a runtime dep of wlrenv with its
entry point redeclared in [project.scripts]. It now installs as a
standalone uv tool via the declarative mechanism added in this branch.
Removes one dep from the wlrenv venv and validates the new mechanism
end-to-end."
```

---

### Task 6: End-to-end verification via `wlr-check-update`

Confirm the full update flow works after migration.

**Files:** none.

- [ ] **Step 1: Run wlr-check-update (or just the post-upgrade hook)**

```bash
"$WLR_ENV_PATH/hooks/post-upgrade"
```

Expected: completes without errors. nix-rebuild is a no-op (manifest unchanged from task 5); installer is a no-op (hyfetch already installed).

- [ ] **Step 2: Verify hyfetch still on PATH**

```bash
which hyfetch
hyfetch --help | head -1
```

Expected: same results as task 5 step 4.

- [ ] **Step 3: Verify nothing else regressed**

Open a fresh shell:

```bash
bash -lc 'echo $PATH | tr : "\n" | head -20; type xonsh hyfetch jq uv'
```

Expected: PATH includes `~/.nix-profile/bin`, `~/.local/bin`; `xonsh`, `hyfetch`, `jq`, `uv` all resolve.

- [ ] **Step 4: No commit needed (verification only)**

If anything in steps 1-3 fails, fix and commit the fix as a separate commit before declaring the prototype done.

---

## Self-Review Notes

**Spec coverage check:**
- Schema (5 fields) → defined in Task 1.
- Platform gating → implemented in Task 1's `resolved` filter.
- Overlay extension → no code change required (uses home-manager's built-in submodule merge); covered implicitly by the option's `attrsOf submodule` type.
- Manifest at `~/.config/wlrenv/uv-tools.json` → Task 1 step 1.
- Installer behavior (per-tool args, failure logging) → Task 3 step 1.
- Hook integration → Task 4.
- hyfetch migration → Task 5.

**Type consistency:** the JSON keys (`spec`, `python`, `withDeps`, `platforms`, `disabled`) are referenced by jq in Task 3 with the exact same names emitted by Nix's `builtins.toJSON` from the option names in Task 1. No camelCase/snake_case drift.

**Placeholder scan:** every step has either an exact code block or an exact command. No "TBD" / "implement later" / "similar to above". Code blocks are full, not truncated.
