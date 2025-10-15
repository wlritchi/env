# Librewolf-niri Workspace Tracking Solution

## Overview

This solution tracks which niri workspace each Librewolf browser window appears on, persists this information to a state file, and restores windows to their original workspaces when Librewolf is restarted.

**Key Difference from tmux Solution:** Unlike tmux sessions which have stable names, browser windows lack persistent identifiers. We use **heuristic matching** based on window titles and tab counts for the proof-of-concept, with future phases supporting URL fingerprinting via browser extension.

---

## Architecture

The solution consists of **3 phases**:

### Phase 1: Proof of Concept (Current)
1. **Workspace Tracker** - Records window-to-workspace mappings via heuristics
2. **Restore Script** - Moves windows to target workspaces (with dry-run support)
3. **Shared niri Helper** - Reuses existing `wlr-niri-move-window-to-workspace`

### Phase 2: Extension Integration (Future)
4. **Browser Extension** - Provides stable window identification via tab URLs
5. **Native Messaging Host** - Bridge between extension and shell scripts

### Phase 3: Polish (Future)
6. **Fuzzy Matching** - Handle partially-matching windows gracefully
7. **Manual Override** - Allow user-defined window names via extension UI

---

## Phase 1: Proof of Concept

### Component 1: Workspace Tracker

**File:** `bin/wayland/wlr-librewolf-track-workspaces`

**Purpose:** Record current Librewolf window → workspace mappings to state file

**Algorithm:**
1. Query niri for all Librewolf windows: `niri msg --json windows`
2. For each Librewolf window:
   - Extract: `window.id`, `window.pid`, `window.title`, `window.workspace_id`
   - Generate heuristic identifier:
     - Primary: Sanitized window title (strip "— LibreWolf" suffix)
     - Fallback: PID (unstable across restarts)
3. Store mappings in JSON state file: `~/.local/share/librewolf/workspace-mappings.json`

**State File Format:**
```json
{
  "version": 1,
  "last_updated": "2025-10-15T12:34:56Z",
  "mappings": [
    {
      "window_title": "NYT Connections #846 | Connections+",
      "workspace_id": 1,
      "pid": 1624025,
      "niri_window_id": 58,
      "timestamp": "2025-10-15T12:34:56Z"
    },
    {
      "window_title": "GitHub - YaLTeR/niri: A scrollable-tiling Wayland compositor",
      "workspace_id": 2,
      "pid": 1624092,
      "niri_window_id": 61,
      "timestamp": "2025-10-15T12:35:12Z"
    }
  ]
}
```

**Execution:**
- Manual: `wlr-librewolf-track-workspaces`
- Automatic: Systemd timer (runs every 30 seconds when niri is active)
- On-demand: Pre-shutdown hook (future enhancement)

---

### Component 2: Restore Script

**File:** `bin/wayland/wlr-librewolf-restore-workspaces`

**Purpose:** Move existing Librewolf windows to their saved workspaces

**Algorithm:**
```bash
#!/usr/bin/env bash
set -euo pipefail

DRY_RUN=false
if [ "${1:-}" = "--dry-run" ]; then
    DRY_RUN=true
fi

STATE_FILE="$HOME/.local/share/librewolf/workspace-mappings.json"

# 1. Load saved mappings from state file
# 2. Query current Librewolf windows from niri
# 3. For each current window:
#    - Match against saved mappings by title heuristic
#    - If match found and workspace differs:
#      - Print planned action
#      - If not dry-run: Move window via wlr-niri-move-window-to-workspace
# 4. Report unmatched windows and obsolete mappings
```

**Matching Strategy (POC):**
1. **Exact title match**: Current window title == saved mapping title
2. **Substring match**: Saved title is substring of current title (handles tab switching)
3. **Prefix match**: First N characters match (handles long/truncated titles)

**Output (Dry-run mode):**
```
[DRY-RUN] Would move window 58 (PID 1624025): "NYT Connections..."
  from workspace 3 → workspace 1

[SKIP] Window 61 (PID 1624092): "GitHub - YaLTeR/niri..."
  already on correct workspace 2

[UNMATCHED] Window 73 (PID 1625001): "Settings — LibreWolf"
  No saved mapping found

[OBSOLETE] Saved mapping: "Firefox Nightly Reviewer's Guide"
  No current window matches (window was closed)
```

**Output (Live mode):**
```
Moved window 58 → workspace 1
Skipped window 61 (already on workspace 2)
Warning: Window 73 has no saved mapping
Warning: Saved mapping "Firefox Nightly..." has no matching window
```

---

### Component 3: Shared Helper (Already Exists)

**File:** `bin/wayland/wlr-niri-move-window-to-workspace`

**Status:** ✅ Already implemented for tmux solution, reusable as-is

**Purpose:** Wait for window to appear by PID and move to target workspace

**Usage:**
```bash
wlr-niri-move-window-to-workspace <pid> <workspace_index>
```

**Notes:**
- For live restoration, we'll use niri window ID directly (not PID)
- Modified usage: `niri msg action move-window-to-workspace --window-id <id> <workspace>`

---

## Phase 2: Extension Integration (Future)

### Component 4: Browser Extension

**Purpose:** Provide stable window identification via tab URL fingerprints

**Capabilities:**
- Track all open windows with their tab URLs
- Generate stable fingerprint: `hash(sorted_urls)`
- Communicate with native messaging host
- Optional: Store metadata in extension storage
- Optional: Trigger restoration on browser startup

**Extension Permissions:**
```json
{
  "permissions": ["tabs", "nativeMessaging"],
  "browser_specific_settings": {
    "gecko": {
      "id": "librewolf-workspace-tracker@wlrenv",
      "strict_min_version": "115.0"
    }
  }
}
```

**Communication Protocol:**
```json
// Extension → Native Host: Query workspace
{
  "action": "get_workspace",
  "window_id": 1234567890,
  "tabs": [
    {"url": "https://github.com/YaLTeR/niri", "title": "GitHub - YaLTeR/niri"},
    {"url": "https://example.com", "title": "Example Domain"}
  ]
}

// Native Host → Extension: Response
{
  "success": true,
  "workspace_id": 2
}
```

---

### Component 5: Native Messaging Host

**File:** `bin/wayland/wlr-librewolf-native-host`

**Purpose:** Bridge between browser extension and niri IPC

**Algorithm:**
1. Read JSON message from stdin (native messaging protocol)
2. Based on action:
   - `get_workspace`: Query niri for window's workspace by PID
   - `store_mapping`: Update state file with URL fingerprint
   - `restore_workspaces`: Trigger restoration script
3. Write JSON response to stdout

**Native Messaging Manifest:**
`~/.librewolf/native-messaging-hosts/wlr_librewolf_workspace_tracker.json`
```json
{
  "name": "wlr_librewolf_workspace_tracker",
  "description": "niri workspace tracking for Librewolf",
  "path": "/home/wlritchi/.wlrenv/bin/wayland/wlr-librewolf-native-host",
  "type": "stdio",
  "allowed_extensions": ["librewolf-workspace-tracker@wlrenv"]
}
```

---

## Phase 3: Polish (Future)

### Component 6: Enhanced Matching

**Features:**
- **Fuzzy matching**: Levenshtein distance for title similarity
- **Multi-window disambiguation**: When multiple windows have similar titles
- **Tab count heuristic**: Prefer matches with similar tab counts
- **URL-based matching**: Primary matching method when extension is available
- **Fallback chain**: URL fingerprint → exact title → fuzzy title → manual

### Component 7: User Interface (Optional)

**Extension UI:**
- Browser action popup showing current window → workspace mapping
- Manual override: "Always open this window on workspace N"
- Custom window names: "Work Browser", "Personal Browser"
- Statistics: Show workspace usage over time

---

## Implementation Strategy: Proof of Concept

### Scope
For the initial POC, we implement **Phase 1 only**:
- ✅ Track windows by title heuristics
- ✅ Store in simple JSON file
- ✅ Restore with dry-run support
- ❌ No extension (yet)
- ❌ No native messaging (yet)
- ❌ No fuzzy matching (yet)

### Limitations Acknowledged
1. **Unstable identifiers**: Window titles change when switching tabs
2. **Collision risk**: Multiple windows may have similar titles
3. **No persistence across major browser restarts**: Requires manual trigger
4. **Manual workflow**: User runs tracker before closing, restore after opening

### Acceptable Trade-offs
- Manual execution is acceptable for POC
- Heuristic matching will work for typical use cases (1-3 windows)
- Extension integration can be added later without rewriting scripts

---

## Deployment

### Systemd Timer Setup

The repository includes a systemd user service/timer that automatically tracks Librewolf windows every 30 seconds when niri is running.

**Configuration:** `machines/systemd-services.nix` (lines 102-130)

The timer is automatically enabled after running `wlr-nix-rebuild`. You can check its status:

```bash
# Check timer status
systemctl --user status wlr-librewolf-niri-tracker.timer
systemctl --user list-timers wlr-librewolf-niri-tracker.timer

# Check service logs
journalctl --user -u wlr-librewolf-niri-tracker.service -f
```

**How it works:**
- Runs every 30 seconds (same as tmux tracker, configurable in nix file)
- Only runs when `NIRI_SOCKET` environment variable is set (i.e., under niri)
- Silently updates the state file in the background
- Low overhead: typically completes in <50ms
- On non-niri machines or X11 sessions, the timer is installed but skips execution

---

## Usage Workflow

### Automatic Tracking (Recommended)

With the systemd timer enabled (default), workspace mappings are automatically updated every 30 seconds. No manual intervention needed for tracking.

### Manual Tracking (Optional)

You can manually trigger tracking before important events:

```bash
# Before intentionally rearranging windows
wlr-librewolf-track-workspaces

# Before system shutdown or browser restart
wlr-librewolf-track-workspaces
```

### Restoration Workflow

After restarting Librewolf (or after windows have moved):

```bash
# Preview what would change
wlr-librewolf-restore-workspaces --dry-run

# Apply the restoration
wlr-librewolf-restore-workspaces

# With verbose output to see matching details
wlr-librewolf-restore-workspaces --verbose
```

**Future (Phase 2):** Browser extension will automatically trigger restoration on startup.

---

## Comparison with tmux Solution

| Aspect | tmux | Librewolf (POC) | Librewolf (Phase 2) |
|--------|------|-----------------|---------------------|
| **Identifier** | Session name | Window title | URL fingerprint |
| **Stability** | ✅ Stable | ⚠️ Changes with tabs | ✅ Stable |
| **Storage** | tmux options + resurrect | JSON state file | JSON state file |
| **Tracking** | PID → session name | Window title | Extension API |
| **Restoration** | Spawn terminal → move | Move existing window | Move existing window |
| **Timing** | On session spawn | After browser start | Automatic on startup |
| **Automation** | Systemd timer | Manual (POC) | Extension-triggered |

---

## Shared Components

Both solutions use:
1. **`wlr-niri-move-window-to-workspace`** - Window movement helper
2. **niri IPC patterns** - JSON queries and window manipulation
3. **State persistence** - Storing workspace mappings externally
4. **Heuristic matching** - Linking ephemeral state to persistent identity

---

## File Locations

### State Files
- **Mappings:** `~/.local/share/librewolf/workspace-mappings.json`
- **Backups:** `~/.local/share/librewolf/workspace-mappings.json.bak`

### Scripts (Phase 1)
- `bin/wayland/wlr-librewolf-track-workspaces` - Capture script
- `bin/wayland/wlr-librewolf-restore-workspaces` - Restore script
- `bin/wayland/wlr-niri-move-window-to-workspace` - Shared helper ✅
- `bin/wayland/wlr-niri-common.bash` - Shared library ✅

### Extension Files (Phase 2+)
- Extension source: `src/librewolf-workspace-tracker/`
- Native host: `bin/wayland/wlr-librewolf-native-host`
- Native manifest: `~/.librewolf/native-messaging-hosts/wlr_librewolf_workspace_tracker.json`

---

## Testing Strategy

### POC Testing
1. **Single window scenario:**
   - Open Librewolf with one window on workspace 2
   - Run tracker
   - Move window to workspace 1 manually
   - Run restore with dry-run → should report "would move to workspace 2"
   - Run restore → window should move back to workspace 2

2. **Multi-window scenario:**
   - Open 3 windows on workspaces 1, 2, 3
   - Track mappings
   - Shuffle windows between workspaces
   - Restore → all windows should return to original workspaces

3. **Title change scenario:**
   - Track window with title "Page A"
   - Switch tab so title becomes "Page B"
   - Restore → should handle gracefully (POC limitation)

4. **New window scenario:**
   - Track 2 windows
   - Open 3rd window
   - Restore → should handle unmatched window gracefully

### Future Testing (Phase 2)
- URL fingerprint stability across tab reordering
- Native messaging communication
- Extension startup restoration
- Multiple Librewolf profiles

---

## Future Enhancements

### Short-term
1. ✅ **Systemd timer**: Auto-track every 30 seconds (implemented)
2. **Pre-shutdown hook**: Integrate with system shutdown
3. **Better matching**: Consider tab count + title length

### Medium-term
4. **Browser extension**: URL fingerprinting
5. **Native messaging**: Automatic restoration
6. **Profile support**: Handle multiple Librewolf profiles

### Long-term
7. **Fuzzy matching**: Handle partially-similar windows
8. **Cross-browser support**: Extend to Firefox, Chrome, etc.
9. **Session integration**: Coordinate with browser session restore
10. **niri integration**: Propose workspace persistence protocol to upstream

---

## Technical Considerations

### Why Not Use PID?
- Browser PIDs change on restart
- Multi-process architecture: window PID ≠ main process PID
- PIDs useful only for same-session tracking

### Why Not Use niri Window ID?
- Window IDs are niri-internal and ephemeral
- Change on every window creation
- Not stable across niri restarts

### Why Heuristics for POC?
- Fastest path to working solution
- No extension development required
- Validates core workflow before investing in complex matching
- Good enough for 1-3 window use cases

### When to Use URL Fingerprinting?
- When user has many windows (4+)
- When windows have similar titles
- When automatic restoration is desired
- When stability matters more than convenience

---

## Design Principles

1. **Progressive enhancement**: POC works without extension, extension adds robustness
2. **Fail-safe**: Dry-run mode prevents accidents, non-destructive operations
3. **Observable**: Clear reporting of matches, mismatches, and actions taken
4. **Reusable**: Shared helpers with tmux solution, extensible to other apps
5. **Low-friction**: Manual workflow acceptable for POC, automation comes later
6. **Data preservation**: State file with backups, never loses mappings

---

## Success Criteria

### POC (Phase 1)
- ✅ Can track 2+ windows on different workspaces
- ✅ Can restore windows to original workspaces
- ✅ Dry-run mode shows accurate plan
- ✅ Handles unmatched windows gracefully
- ✅ State file is human-readable JSON

### Extension Integration (Phase 2)
- ✅ Stable identification via URLs
- ✅ Automatic restoration on browser start
- ✅ No false positives in matching

### Production Ready (Phase 3)
- ✅ Fuzzy matching handles edge cases
- ✅ Systemd integration for automation
- ✅ Extension published (optional)
- ✅ Documentation for other users

---

## Related Documentation

- **Tmux solution:** `TMUX_NIRI_WORKSPACE_TRACKING.md`
- **niri IPC:** https://github.com/YaLTeR/niri/wiki/IPC
- **WebExtensions API:** https://developer.mozilla.org/en-US/docs/Mozilla/Add-ons/WebExtensions
- **Native Messaging:** https://developer.mozilla.org/en-US/docs/Mozilla/Add-ons/WebExtensions/Native_messaging
