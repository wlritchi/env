# Declarative `uv tool install` — Design

## Goal

Provide a declarative mechanism to install Python CLIs via `uv tool install`, with per-tool isolation, version pinning, extra deps, platform gating, and private-overlay extension.

Today: there is no general mechanism. The only `uv tool install` in the repo is `env.bash:343`, which installs the wlrenv project itself (so its `[project.scripts]` entries land on PATH). Other Python CLIs are pulled in by listing them as wlrenv runtime deps and re-exporting their entry points from `pyproject.toml` (the `hyfetch` pattern). This forces all tools to share a single Python version and a single venv, requires looking up internal `module:func` paths for each re-export, and offers no clean overlay surface.

## Approach

Declare tools in a Nix attrset. Render the resolved set (after platform gating + overlay merge) to a JSON manifest under `~/.config/wlrenv/`. A small installer script reads the manifest and runs `uv tool install` for each entry. The installer runs from the post-upgrade hook, not on shell startup.

The bootstrap install in `env.bash` keeps its single responsibility: install the wlrenv project so `xonsh` is on PATH for the immediate `exec xonsh`. Everything else moves to the new mechanism.

## Schema

`home.uvTools` is a home-manager option, an attrset keyed by tool name (the manifest's identity for merge purposes — *not* necessarily the package name on PyPI). Each value is a submodule:

| Field       | Type             | Default              | Notes                                                                                  |
|-------------|------------------|----------------------|----------------------------------------------------------------------------------------|
| `spec`      | string           | required             | What to pass as the package argument to `uv tool install`. PyPI spec, `git+`, or path. |
| `python`    | string \| null   | `null`               | Passed as `--python <value>` if non-null.                                              |
| `withDeps`  | list of strings  | `[]`                 | Each becomes a separate `--with <dep>`.                                                |
| `platforms` | list of enum     | `["linux" "darwin"]` | Tool only installed on listed platforms.                                               |
| `disabled`  | bool             | `false`              | Overlay can disable a tool the public list defined.                                    |

Why these fields: see the conversation that produced this spec. `spec` and `platforms` are essentials; `python` and `withDeps` cover the realistic per-tool variation seen in Python CLI tooling; `disabled` is the escape hatch for an overlay to suppress a public entry without removing it. `--from` is intentionally omitted: `uv tool install foo` already exposes `foo`'s entry points by default, and the niche case (forks that don't rename) is not worth the schema surface today.

## Where things live

| Path                                   | Purpose                                                               |
|----------------------------------------|-----------------------------------------------------------------------|
| `machines/uv-tools.nix`                | Defines the `home.uvTools` option, populates the public list, renders the manifest via `home.file`. Imported from `common.nix`. |
| `~/.config/wlrenv/uv-tools.json`       | Generated manifest. Symlink to nix store. Read by the installer.      |
| `bin/meta/wlr-uv-tools-install`        | Installer. Iterates the manifest and runs `uv tool install` per entry. |
| `hooks/post-upgrade`                   | Calls the installer after `wlr-nix-rebuild`.                          |

## Overlay extension

The private overlay (`~/.wlrenv-private`) sets `home.uvTools.<name> = { ... }` in its own home-manager module. Home-manager's option-merging semantics handle this for free:

- **Add a new tool**: overlay sets `home.uvTools.aider = { spec = "aider-chat"; }`. Public list is unchanged.
- **Override fields on an existing tool**: overlay sets `home.uvTools.hyfetch.python = "3.12"`. Other fields inherit from the public definition.
- **Suppress a public tool**: overlay sets `home.uvTools.hyfetch.disabled = true`. The renderer filters it out.

No new merge logic in this repo — submodule attrset merging in home-manager is already the right behavior.

## Platform gating

The renderer reads `pkgs.stdenv.isDarwin` to determine the current platform, then filters out entries whose `platforms` list does not include the current platform. Filtering happens at render time so the manifest is already platform-resolved when the installer reads it.

Cross-platform QA via `NIX_SYSTEM` override (existing pattern, see `wlr-nix-rebuild`) naturally tests the gating because each system render produces a different manifest.

## Manifest format

A flat JSON object keyed by tool name:

```json
{
  "hyfetch": {
    "spec": "hyfetch>=2.0.0,<3.0.0",
    "python": null,
    "withDeps": [],
    "platforms": ["linux", "darwin"],
    "disabled": false
  }
}
```

The renderer emits *all* fields (no omission of nulls/empties) so the installer's parsing path is uniform. Disabled entries are filtered out at render time and never appear in the manifest.

## Installer behavior

For each entry in the manifest, the installer constructs a `uv tool install` invocation:

```
uv tool install --quiet [--python <python>] [--with <dep>...] <spec>
```

`uv tool install` is idempotent for unchanged inputs (cached install), so running the installer on every post-upgrade is cheap. Failure to install one tool logs an error via `wlr-err` but does not abort the loop — other tools should still get a chance to install.

The installer does **not** uninstall tools that have been removed from the manifest. That is a future concern; in practice, dropping a tool from the list and waiting for `uv tool list` to show stale entries is fine for now.

## Migration: `hyfetch`

`hyfetch` is the test case for the prototype. It is currently:

1. Listed in `pyproject.toml` `[project.dependencies]`.
2. Re-exported in `pyproject.toml` `[project.scripts]` as `hyfetch = 'hyfetch.__main__:run_rust'`.

After migration:

1. Both pyproject.toml entries are removed.
2. `machines/uv-tools.nix` declares `home.uvTools.hyfetch = { spec = "hyfetch>=2.0.0,<3.0.0"; }`.
3. After `wlr-nix-rebuild` + `wlr-uv-tools-install`, `hyfetch` is on PATH as a standalone uv tool with its own venv.

Verification: `which hyfetch` should resolve to a path under `~/.local/share/uv/tools/hyfetch/`-derived bin directory (uv's default tool install location), `hyfetch --help` should run.

## Out of scope (deferred)

- **Per-tool lockfiles.** The schema does not capture lockable dependency state. Tools resolve fresh at install time; reproducibility is bounded by the spec string.
- **Path-spec stub packages.** A future `spec = "path:./uv-tools/foo"` could point at a stub pyproject.toml for tools needing complex configuration. The installer doesn't need changes to support this — `uv tool install <path>` already works.
- **Renovate-friendly version bumps.** Spec strings live in Nix and are not auto-bumped today. Could be addressed later by switching to per-tool stubs for high-churn tools.
- **Uninstall tracking.** Stale `uv tool` entries are not pruned automatically.
