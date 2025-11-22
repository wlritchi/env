# Gitconfig Setup Design

## Overview

Set up a common base gitconfig synced in the repo with support for:
- Machine-specific versioned overrides (user.email)
- Machine-specific unversioned config (user.signingKey, maintenance)
- Automated setup assistance via post-upgrade hook

## Design

### File Structure

```
config/git/
    common                    # Common config (aliases, colors, settings)
    hosts/
        neon                  # Host-specific: user.email
        amygdalin             # Host-specific: user.email
    local.template            # Template for ~/.gitconfig

~/.gitconfig                  # Local (untracked) - includes + signingKey + maintenance
```

Files live in `config/git/` (not `dotfiles/`) to avoid symlink sync.

### Include Layering

Git's config layering means later values override earlier ones:

```ini
# ~/.gitconfig (local, untracked)
[include]
    path = ~/.wlrenv/config/git/common
    path = ~/.wlrenv/config/git/hosts/neon   # if exists for this host

[user]
    signingKey = ABC123...

# [maintenance] section written here by git maintenance start
```

### Why This Approach

- **Inverted include**: `~/.gitconfig` is local (not symlinked) so `git maintenance start` can edit it freely
- **Multiple includes over patches**: Uses git's native mechanism, avoids patch fragility
- **Split versioning**: user.email versioned per-host, signingKey stays local (may contain paths or secrets)
- **Tilde expansion**: Git expands `~` in include paths, so includes are portable

### Setup Automation

Post-upgrade hook in `hooks/post-upgrade`:

```bash
if [ ! -f ~/.gitconfig ]; then
    cp "$WLR_ENV_PATH/config/git/local.template" ~/.gitconfig
    wlr-warn "Created ~/.gitconfig from template - edit user.signingKey and HOSTNAME"
elif ! grep -q 'path.*\.wlrenv/config/git/common' ~/.gitconfig; then
    wlr-warn "~/.gitconfig missing include for common gitconfig"
fi
```

- Fresh machines: automatically creates from template
- Existing setups: warns if missing include (for manual migration)

### Template Contents

`config/git/local.template`:

```ini
[include]
    path = ~/.wlrenv/config/git/common
    path = ~/.wlrenv/config/git/hosts/HOSTNAME

[user]
    signingKey = YOUR_SIGNING_KEY_HERE
```

User replaces `HOSTNAME` and `YOUR_SIGNING_KEY_HERE` after creation.

## Implementation Tasks

1. Create `config/git/common` with common settings (migrate from existing configs)
2. Create `config/git/hosts/` with per-host identity files
3. Create `config/git/local.template`
4. Add detection/setup logic to `hooks/post-upgrade`
5. Migrate existing machines (backup old config, create new structure)
