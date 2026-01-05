# Private Dotfiles Overlay Design

## Problem

The `.wlrenv-private` repo can overlay Nix configuration but not dotfiles. Work machines need private dotfiles (e.g., tool configs with internal hostnames) that can't go in the public repo.

## Solution

Extend `wlr-sync-dotfiles` to support a priority chain: private dotfiles shadow public ones, and private patches can layer on top of public files.

## File Resolution Logic

For each dotfile path:

**Step 1: Determine base file**
- If `~/.wlrenv-private/dotfiles/$path` exists → use it (source = private)
- Else if `~/.wlrenv/dotfiles/$path` exists → use it (source = public)
- Else skip

**Step 2: Collect patches (general → specific order)**

If source is private (public file + patches are fully shadowed):
```
~/.wlrenv-private/patches/uname/$(uname)/$path.patch
~/.wlrenv-private/patches/host/$HOSTNAME/$path.patch
```

If source is public (private patches layer on top):
```
~/.wlrenv/patches/uname/$(uname)/$path.patch
~/.wlrenv/patches/host/$HOSTNAME/$path.patch
~/.wlrenv-private/patches/uname/$(uname)/$path.patch
~/.wlrenv-private/patches/host/$HOSTNAME/$path.patch
```

Only patches that exist are applied.

**Step 3: Determine rendered location**
- If any private content involved → `~/.wlrenv-private/rendered/$path`
- Otherwise → `~/.wlrenv/rendered/$path`

## File Discovery

Scan both repos and deduplicate:
```bash
paths = unique(
    find ~/.wlrenv/dotfiles -type f,
    find ~/.wlrenv-private/dotfiles -type f  # if exists
)
```

## Implementation Changes

Modify `bin/meta/wlr-sync-dotfiles`:

1. Add private repo detection at top
2. Update `render_file()` to:
   - Determine source (private vs public)
   - Build patch list based on source
   - Choose rendered directory based on private involvement
3. Update file discovery to scan both repos
4. Driveby fix: change patch order from host→uname to uname→host (more general first)

Functions `sync_dir()` and `link_file()` remain unchanged.

## Edge Cases

- **Patch conflict**: Fails fast (existing behavior), user fixes manually
- **Missing private repo**: Only public dotfiles apply (no warnings)
- **Empty private dotfiles dir**: Fine; private patches still apply to public files
- **Stale rendered files**: Extend staleness check to include private patches

## Out of Scope

- Verbose/debug output (can add later if needed)
- Automatic conflict resolution
