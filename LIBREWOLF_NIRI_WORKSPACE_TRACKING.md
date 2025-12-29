# Librewolf-niri Workspace Tracking Solution

## Overview

This solution tracks which niri workspace each Librewolf browser window appears on, persists this information to a state file, and restores windows to their original workspaces when Librewolf is restarted.

## Architecture

The solution consists of:

1. **Browser Extension** (`src/librewolf-workspace-tracker/`) - Collects window/tab information
2. **Native Messaging Host** (`wlr-niri-librewolf-host`) - Python host that bridges extension to niri
3. **Shared Position Storage** (`~/.local/state/niri/positions.json`) - Boot-keyed position data
4. **URL Identity Storage** (`~/.local/state/niri/librewolf-identities.json`) - URL→UUID mappings

### Data Flow

```
Extension (JS) ──native messaging──> Native Host (Python) ──IPC──> niri
                                           │
                                           ├──> positions.json (workspace/column/width)
                                           └──> librewolf-identities.json (URL→UUID)
```

## Components

### Browser Extension

The extension (`src/librewolf-workspace-tracker/`) provides:

- **Tab URL collection**: Gathers URLs from all tabs in each window
- **Periodic sync**: Sends window state to native host every 30 seconds
- **Startup restoration**: Triggers restoration 3 seconds after browser start

### Native Messaging Host

The Python host (`wlr-niri-librewolf-host`, installed via `uv tool install`) handles:

- **`store_mappings_batch`**: Match browser windows to niri windows by title, store positions
- **`restore_workspaces`**: Look up saved positions, move windows back

### Storage Files

**`~/.local/state/niri/positions.json`** - Shared with tmux/mosh tracking:
```json
{
  "version": 1,
  "boots": {
    "uuid-of-boot": {
      "updated_at": "2025-01-01T12:00:00Z",
      "apps": ["librewolf", "tmux"],
      "workspaces": {
        "1": [
          {"id": "librewolf:uuid", "index": 0, "window_id": 123, "width": 50}
        ]
      }
    }
  }
}
```

**`~/.local/state/niri/librewolf-identities.json`** - URL→UUID mappings:
```json
{
  "version": 1,
  "entries": [
    {"uuid": "...", "urls": ["https://github.com/...", "https://..."]}
  ]
}
```

## Installation

See `src/librewolf-workspace-tracker/README.md` for detailed installation instructions.

Quick summary:
1. Run `wlr-nix-rebuild` to deploy the native messaging manifest
2. Install the extension XPI from `~/.wlrenv/build/librewolf-workspace-tracker/`
3. Ensure `wlr-niri-librewolf-host` is available (via `uv tool install`)

## How Matching Works

### Store Flow
1. Extension sends window titles + tab URLs
2. Native host uses `UrlMatcher` to find/create stable UUID for each window's URL set
3. Native host matches browser windows to niri windows by title
4. Positions are stored with stable ID `librewolf:{uuid}`

### Restore Flow
1. Extension sends current window titles + tab URLs
2. Native host looks up stable IDs via `UrlMatcher`
3. Positions are retrieved from `positions.json`
4. Windows are moved to saved workspaces and column-ordered

### URL Matching

The `UrlMatcher` uses set intersection to match windows:
- Compares current tab URLs against stored URL sets
- Best overlap wins (greedy matching)
- URLs are updated on each store to track tab changes

## Debugging

Enable logging by setting `NIRI_DEBUG=1` or `NIRI_DEBUG=DEBUG`:
```bash
export NIRI_DEBUG=1
```

Logs are written to `~/.local/state/niri/librewolf-host.log`.

## Comparison with tmux Solution

| Aspect | tmux | Librewolf |
|--------|------|-----------|
| **Identifier** | Session name | URL fingerprint (UUID) |
| **Stability** | Stable | Stable (via URLs) |
| **Storage** | positions.json | positions.json |
| **Tracking** | systemd timer | Browser extension |
| **Restoration** | On session spawn | On browser startup |

Both solutions share `positions.json` and use the same predecessor-based ordering system.

## File Locations

| File | Purpose |
|------|---------|
| `~/.local/state/niri/positions.json` | Boot-keyed position data |
| `~/.local/state/niri/librewolf-identities.json` | URL→UUID mappings |
| `~/.local/state/niri/librewolf-host.log` | Debug logs (when enabled) |
| `~/.librewolf/native-messaging-hosts/wlr_librewolf_workspace_tracker.json` | Native messaging manifest |
| `~/.wlrenv/build/librewolf-workspace-tracker/librewolf-workspace-tracker.xpi` | Extension package |

## Related Documentation

- **Extension README:** `src/librewolf-workspace-tracker/README.md`
- **Tmux solution:** `TMUX_NIRI_WORKSPACE_TRACKING.md`
- **niri IPC:** https://github.com/YaLTeR/niri/wiki/IPC
- **WebExtensions API:** https://developer.mozilla.org/en-US/docs/Mozilla/Add-ons/WebExtensions
- **Native Messaging:** https://developer.mozilla.org/en-US/docs/Mozilla/Add-ons/WebExtensions/Native_messaging
