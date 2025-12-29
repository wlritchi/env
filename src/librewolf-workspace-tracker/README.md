# LibreWolf niri Workspace Tracker Extension

This WebExtension tracks LibreWolf windows across niri workspaces, enabling automatic restoration of window positions after browser restart.

## Overview

The extension provides:

- **Stable URL-based identification**: Windows are identified by their tab URLs via a UrlMatcher
- **Automatic tracking**: Syncs window-to-workspace mappings every 30 seconds
- **Automatic restoration**: Restores windows to saved workspaces on browser startup
- **Native messaging**: Communicates with niri via `wlr-niri-librewolf-host`

## Architecture

The extension communicates with a Python native messaging host (`wlr-niri-librewolf-host`) which:
- Queries niri for window state via IPC
- Stores position data in `~/.local/state/niri/positions.json` (shared with tmux/mosh tracking)
- Maintains URL→UUID mappings in `~/.local/state/niri/librewolf-identities.json`

## Installation

### 1. Build and Install via Nix

The extension is automatically built when you run `wlr-nix-rebuild`:

```bash
wlr-nix-rebuild
```

This will:
- Build the extension XPI package
- Deploy the native messaging manifest to `~/.librewolf/native-messaging-hosts/`

### 2. Install the Extension in LibreWolf

Since the extension is unsigned, you need to manually install it:

1. Open LibreWolf
2. Navigate to `about:addons`
3. Click the gear icon → "Install Add-on From File..."
4. Browse to `~/.wlrenv/build/librewolf-workspace-tracker/librewolf-workspace-tracker.xpi`
5. Click "Add" when prompted

**Note**: LibreWolf may require enabling unsigned extensions:
- Navigate to `about:config`
- Set `xpinstall.signatures.required` to `false`

### 3. Verify Installation

After installing the extension:

1. Open the Browser Console (`Ctrl+Shift+J`)
2. Look for messages like:
   ```
   LibreWolf niri Workspace Tracker loaded
   Connected to native host
   ```

## Usage

Once installed, the extension works automatically:

### Automatic Tracking
- Updates every 30 seconds
- Stores mappings via the native host

### Automatic Restoration
- On browser startup, waits 3 seconds for windows to load
- Sends restoration request to native host
- Windows are moved back to their saved workspaces

## Debugging

Enable debug logging by setting `NIRI_DEBUG=1` (or `NIRI_DEBUG=DEBUG` for verbose):
```bash
export NIRI_DEBUG=1
```

Logs are written to `~/.local/state/niri/librewolf-host.log`.

## Components

- **manifest.json**: WebExtension manifest with permissions
- **native-messaging.js**: Communication layer with native host
- **background.js**: Tab tracking and URL collection
- **restoration.js**: Automatic restoration on startup
- **icons/**: Extension icons

## See Also

- [LIBREWOLF_NIRI_WORKSPACE_TRACKING.md](../../LIBREWOLF_NIRI_WORKSPACE_TRACKING.md) - Design documentation
- [TMUX_NIRI_WORKSPACE_TRACKING.md](../../TMUX_NIRI_WORKSPACE_TRACKING.md) - Related tmux solution
