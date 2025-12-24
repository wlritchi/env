# Private Configuration & Secrets Management Design

## Problem

The `.wlrenv` repository is public and cannot contain:
- Private details (internal hostnames, company tool configs, project names)
- Secrets (API keys, tokens, credentials)

Goal: Declaratively recover a full system environment by cloning one or two repos.

## Solution Overview

Three-tier approach:

1. **Public repo (`.wlrenv`)** - General config, shared across all machines
2. **Private repo (`.wlrenv-private`)** - Work-specific config, extends public repo
3. **Secrets in pass/passage** - Accessed on-demand via `secwrap` wrapper

## Repository Structure

### Public Repo (`.wlrenv`)

Unchanged, plus:
- `secwrap` tool (backend parameterized via Nix)
- Shell aliases that wrap commands through secwrap
- Orchestration scripts aware of private repo existence

### Private Repo (`.wlrenv-private`)

- Work-specific tool configs (teleport, acli, Cloudsmith config files)
- Private-but-not-secret environment setup
- Nix flake that imports and extends `.wlrenv`
- Own `.gitallowedsigners` (Secretive-backed SSH key)
- Optional `hooks/post-upgrade` for work-specific tasks

### Secrets (in pass/passage)

Organized by tool name:
```
config/env/aws
config/env/cloudsmith
config/env/claude
```

Each entry contains `KEY=VALUE` lines:
```
AWS_ACCESS_KEY_ID=AKIA...
AWS_SECRET_ACCESS_KEY=wJalr...
```

## Activation Flow

`wlr-nix-rebuild` detects private repo:

```bash
if [[ -d "$HOME/.wlrenv-private" ]]; then
    home-manager switch --flake "$HOME/.wlrenv-private"
else
    home-manager switch --flake "$HOME/.wlrenv"
fi
```

## Update Flow

Public repo orchestrates updates for both repos:

1. Read allowed signers from current (validated) HEAD of each repo
2. Fetch new commits for public repo
3. Validate public repo commits against its allowed signers
4. If private repo exists:
   - Fetch new commits
   - Validate against private repo's allowed signers
5. Run public repo hooks (includes `wlr-nix-rebuild`)
6. Run private repo hooks if present

**Security note:** Allowed signers must be read from the validated state BEFORE checking out new commits.

## Private Repo Flake Structure

```nix
# ~/.wlrenv-private/flake.nix
{
  inputs = {
    wlrenv.url = "path:/home/luc.ritchie/.wlrenv";
  };

  outputs = { wlrenv, ... }: {
    homeConfigurations."luc.ritchie" =
      wlrenv.homeConfigurations."luc.ritchie".extendModules {
        modules = [ ./work-config.nix ];
      };
  };
}
```

## The `secwrap` Tool

### Behavior

```bash
secwrap aws s3 ls
# 1. tool_name = "aws"
# 2. Try: $backend show "config/env/aws"
# 3. If exists: parse KEY=VALUE lines, export each
# 4. If not found: no-op (tool doesn't need secrets)
# 5. exec aws s3 ls
```

### Nix Integration

```nix
# Backend baked in at build time
programs.secwrap = {
  enable = true;
  backend = "passage";  # or "pass"
};
```

### Shell Aliases

Hardcoded in shell configs, conditioned on secwrap availability:

```bash
# env.bash
if command -v secwrap &>/dev/null; then
    alias aws='secwrap aws'
    alias cloudsmith='secwrap cloudsmith'
    alias claude='secwrap claude'
fi
```

```python
# xonshrc.py
if shutil.which('secwrap'):
    aliases['aws'] = 'secwrap aws'
    # etc.
```

### Benefits

- Single TouchID prompt per tool (not per secret)
- Secrets never in git (encrypted or otherwise)
- Same workflow for pass (home) and passage (work)
- No activation-time decryption needed

## Signature Validation

Each repo has its own allowed signers:
- **Public repo**: Yubikey-backed key (used on all machines)
- **Private repo**: Secretive-backed SSH key (work machine only)

Validation logic reads signers from the repo being validated:

```bash
validate_repo() {
    local repo_path="$1"
    local allowed_signers="$repo_path/.gitallowedsigners"
    # validate using that repo's allowed signers
}
```

## Out of Scope

- 1Password integration (optional future enhancement)
- Fixing sops-nix/age-plugin-se upstream
- Activation-time secret decryption

## Implementation Steps

1. Implement `secwrap` tool (bash script or Rust utility)
2. Add Nix module for secwrap with backend parameter
3. Add shell aliases in env.bash and xonshrc.py
4. Modify `wlr-nix-rebuild` to detect private repo
5. Modify update/validation scripts to handle both repos
6. Create `.wlrenv-private` repo structure
7. Set up passage entries for work secrets
