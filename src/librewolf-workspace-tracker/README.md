# LibreWolf niri Workspace Tracker Extension

This WebExtension provides stable window identification for LibreWolf using URL fingerprints, enabling accurate workspace restoration across browser restarts when running under the niri window manager.

## Overview

This extension is **Phase 2** of the LibreWolf workspace tracking solution. It provides:

- **Stable URL-based fingerprinting**: Windows are identified by their tab URLs, not just titles
- **Automatic tracking**: Syncs window-to-workspace mappings every 30 seconds
- **Automatic restoration**: Restores windows to saved workspaces on browser startup
- **Native messaging**: Communicates with niri via a native host script

See [LIBREWOLF_NIRI_WORKSPACE_TRACKING.md](../../LIBREWOLF_NIRI_WORKSPACE_TRACKING.md) for full architecture documentation.

## Installation

### 1. Build and Install via Nix

The extension is automatically built and prepared when you run `wlr-nix-rebuild`:

```bash
wlr-nix-rebuild
```

This will:
- Build the extension XPI package
- Install it to `~/.local/share/librewolf-workspace-tracker/`
- Deploy the native messaging manifest to `~/.librewolf/native-messaging-hosts/`
- Ensure the native host script is executable

### 2. Install the Extension in LibreWolf

Since the extension is unsigned, you need to manually install it:

1. Open LibreWolf
2. Navigate to `about:addons`
3. Click the gear icon ⚙️ → "Install Add-on From File..."
4. Browse to `~/.local/share/librewolf-workspace-tracker/librewolf-workspace-tracker.xpi`
5. Click "Add" when prompted

**Note**: LibreWolf may require you to enable installation of unsigned extensions:
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

If you see connection errors, check that:
- You're running under niri (`echo $NIRI_SOCKET` should output a path)
- The native host script exists at `~/.wlrenv/bin/wayland/wlr-librewolf-native-host`
- The native messaging manifest exists at `~/.librewolf/native-messaging-hosts/wlr_librewolf_workspace_tracker.json`

## Usage

Once installed, the extension works automatically:

### Automatic Tracking

The extension tracks all your windows and their tab URLs:
- Updates every 30 seconds (configurable in `background.js`)
- Stores mappings in `~/.local/share/librewolf/workspace-mappings.json`
- Includes URL fingerprints for stable identification

### Automatic Restoration

When you restart LibreWolf:
- The extension waits 3 seconds for all windows to load
- It sends a restoration request to the native host
- Windows are moved back to their saved workspaces
- You'll see a notification showing how many windows were moved

### Manual Restoration

You can trigger restoration manually from the Browser Console:

```javascript
browser.runtime.sendMessage({action: "restore_now"})
```

### State File Format

The extension stores window mappings in version 2 format with URL fingerprints:

```json
{
  "version": 2,
  "last_updated": "2025-10-15T17:30:00Z",
  "mappings": [
    {
      "window_id": 1234,
      "workspace_id": 2,
      "fingerprint": "sha256:abc123...",
      "window_title": "GitHub - YaLTeR/niri",
      "tab_urls": [
        {"url": "https://github.com/YaLTeR/niri", "title": "GitHub..."}
      ],
      "tab_count": 5,
      "timestamp": "2025-10-15T17:30:00Z"
    }
  ]
}
```

## Troubleshooting

### Extension not connecting to native host

Check the Browser Console for errors. Common issues:

1. **Not running under niri**: The native host requires `NIRI_SOCKET` to be set
2. **Native messaging manifest not found**: Run `wlr-nix-rebuild` to deploy it
3. **Native host script not executable**: Check `ls -la ~/.wlrenv/bin/wayland/wlr-librewolf-native-host`

### Windows not being restored

1. **Check if mappings exist**:
   ```bash
   cat ~/.local/share/librewolf/workspace-mappings.json
   ```

2. **Manually test restoration**:
   ```bash
   wlr-librewolf-restore-workspaces --dry-run --verbose
   ```

3. **Check native host logs**:
   The native host logs to stderr, which LibreWolf captures in the Browser Console

### State file conflicts

If you see conflicts between the extension and the manual tracker:
- The extension (Phase 2) takes priority - it provides URL fingerprints
- The manual tracker (Phase 1) still works as a fallback
- Both write to the same state file in version 2 format

## Development

### Debugging

Enable verbose logging in the Browser Console (`Ctrl+Shift+J`).

All extension components log with prefixes:
- `[native-messaging.js]` - Native host communication
- `[background.js]` - Tab tracking and fingerprinting
- `[restoration.js]` - Automatic restoration

### Modifying the Extension

After making changes to the extension source:

1. Rebuild with Nix:
   ```bash
   wlr-nix-rebuild
   ```

2. Reload the extension in LibreWolf:
   - Go to `about:debugging#/runtime/this-firefox`
   - Click "Reload" next to "LibreWolf niri Workspace Tracker"

### Testing Native Messaging

You can test the native host directly using the test helper (which handles the native messaging protocol):

```bash
# Ping test
wlr-librewolf-native-host-test '{"request_id": "test1", "action": "ping"}'

# Get workspace test (requires NIRI_SOCKET and running LibreWolf windows)
wlr-librewolf-native-host-test '{"request_id": "test2", "action": "get_workspace", "window_id": 123, "tabs": []}'

# Manual test with proper protocol (4-byte length prefix + JSON):
# Note: The native messaging protocol requires a 4-byte little-endian length prefix
# Use the test helper above for easier testing
```

## Components

- **manifest.json**: WebExtension manifest with permissions
- **native-messaging.js**: Communication layer with native host
- **background.js**: Tab tracking and URL fingerprinting
- **restoration.js**: Automatic restoration on startup
- **icons/**: Extension icons (placeholder blue "W")

## See Also

- [LIBREWOLF_NIRI_WORKSPACE_TRACKING.md](../../LIBREWOLF_NIRI_WORKSPACE_TRACKING.md) - Full solution documentation
- [TMUX_NIRI_WORKSPACE_TRACKING.md](../../TMUX_NIRI_WORKSPACE_TRACKING.md) - Related tmux solution
- [wlr-librewolf-native-host](../../bin/wayland/wlr-librewolf-native-host) - Native messaging host script
- [wlr-librewolf-restore-workspaces](../../bin/wayland/wlr-librewolf-restore-workspaces) - Restoration script
