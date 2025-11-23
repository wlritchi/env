# tmux-niri Workspace and Width Tracking Solution

## Overview

This solution tracks which niri workspace each tmux session's terminal appears on, as well as the window width (as a percentage of screen width), persists this information across tmux restarts, and restores sessions to their original workspaces and widths on reboot.

## Architecture

The solution consists of **4 components**:

1. **Workspace/Width Tracker** - Monitors and records session-to-workspace and session-to-width mappings
2. **tmux-resurrect Integration** - Persists workspace and width data across restarts
3. **Restore Script** - Spawns sessions on correct workspaces with correct widths
4. **niri IPC Helper** - Utility for configuring windows (workspace, width, etc.)

---

## Component 1: Workspace/Width Tracker

**File:** `bin/tmux/wlr-tmux-niri-tracker`

**Purpose:** Periodically map tmux sessions to niri workspaces and window widths, store in tmux user options

**Algorithm:**
1. Query niri for output dimensions: `niri msg --json outputs`
2. Query niri for workspace-to-output mapping: `niri msg --json workspaces`
3. Query niri for all Alacritty windows: `niri msg --json windows`
4. For each Alacritty window:
   - Get: `window.pid → window.workspace_id`, `window.layout.tile_size[0]`
   - Find child tmux client: `pgrep -P <alacritty_pid>`
   - Extract tmux session from client cmdline: `ps -o args`
   - Calculate width percentage: `tile_width / monitor_width * 100`, rounded to nearest 10%
   - Set: `tmux set-option -t <session> @niri-workspace <workspace_id>`
   - Set: `tmux set-option -t <session> @niri-width <width_percent>`
5. Sleep and repeat (or run via systemd timer)

**Key Challenge:** Linking alacritty PID → tmux session
- Alacritty spawns as: `alacritty -e tmux attach-session -t <session>`
- Child process is: `tmux attach-session -t <session>`
- Parse session name from child process arguments

**Execution:** Can run as:
- Periodic script (cron/systemd timer)
- Background daemon with niri event stream
- tmux status-right periodic command

---

## Component 2: tmux-resurrect Integration

### 2a. Save Hook

**File:** `bin/tmux/wlr-tmux-resurrect-save-workspaces`

**Purpose:** Append workspace and width mappings to resurrect save file

**Configuration:** Add to `.tmux.conf`:
```bash
set -g @resurrect-hook-post-save-layout 'wlr-tmux-resurrect-save-workspaces'
```

**Algorithm:**
```bash
#!/bin/bash
SAVE_FILE="$1"  # Passed by resurrect hook

# Append custom lines for each session with workspace and width data
tmux list-sessions -F '#{session_name}' | while read session; do
    workspace=$(tmux show-options -t "$session" -v @niri-workspace 2>/dev/null)
    width=$(tmux show-options -t "$session" -v @niri-width 2>/dev/null)
    if [ -n "$workspace" ]; then
        echo "niri_workspace${TAB}${session}${TAB}${workspace}" >> "$SAVE_FILE"
    fi
    if [ -n "$width" ]; then
        echo "niri_width${TAB}${session}${TAB}${width}" >> "$SAVE_FILE"
    fi
done
```

### 2b. Restore Hook

**File:** `bin/tmux/wlr-tmux-resurrect-restore-workspaces`

**Purpose:** Restore @niri-workspace and @niri-width options from save file

**Configuration:** Add to `.tmux.conf`:
```bash
set -g @resurrect-hook-pre-restore-pane-processes 'wlr-tmux-resurrect-restore-workspaces'
```

**Algorithm:**
```bash
#!/bin/bash
# Runs AFTER sessions are created but BEFORE processes are started
SAVE_FILE="$HOME/.local/share/tmux/resurrect/last"

# Read and restore workspace options
grep "^niri_workspace" "$SAVE_FILE" | while IFS=$'\t' read _ session workspace; do
    tmux set-option -t "$session" @niri-workspace "$workspace" 2>/dev/null || true
done

# Read and restore width options
grep "^niri_width" "$SAVE_FILE" | while IFS=$'\t' read _ session width; do
    tmux set-option -t "$session" @niri-width "$width" 2>/dev/null || true
done
```

---

## Component 3: Enhanced Session Opener

**File:** `bin/tmux/wlr-open-tmux-sessions` (modified)

**Purpose:** Open detached sessions on their original workspaces with original widths

**Enhanced Algorithm:**
```bash
#!/bin/bash
set -euo pipefail

term="${TERMINAL:-alacritty}"
readarray -t detached_sessions < <( tmux list-sessions -F '#{session_name}' -f '#{?#{session_attached},0,1}' )

# Helper function to spawn and configure window
spawn_and_configure() {
    local session="$1"
    local workspace="$2"
    local width="$3"

    # Spawn terminal
    "$term" -e tmux attach-session -t "$session" &
    local alacritty_pid=$!

    # Build arguments for configure script
    local args=()
    if [ -n "$workspace" ]; then
        args+=(--workspace "$workspace")
    fi
    if [ -n "$width" ]; then
        args+=(--width "$width")
    fi

    # Configure window if we have any options to set
    if [ ${#args[@]} -gt 0 ]; then
        wlr-niri-configure-window "$alacritty_pid" "${args[@]}" 2>/dev/null || true
    fi
}

for session in "${detached_sessions[@]}"; do
    if ! echo "$session" | grep -Eq '^[0-9a-f]{7}$'; then
        # Get saved workspace and width
        workspace=$(tmux show-options -t "$session" -v @niri-workspace 2>/dev/null || true)
        width=$(tmux show-options -t "$session" -v @niri-width 2>/dev/null || true)
        spawn_and_configure "$session" "$workspace" "$width"
    fi
done
```

---

## Component 4: niri IPC Helper

**File:** `bin/wayland/wlr-niri-configure-window`

**Purpose:** Wait for window to spawn and configure it (workspace, width, etc.)

**Usage:**
```bash
wlr-niri-configure-window <pid> [--workspace <id>] [--width <percent>]
```

All options are optional; only PID is required. The script exits early if no options are provided.

**Algorithm:**
```bash
#!/bin/bash
set -euo pipefail

pid="$1"
# Parse --workspace and --width options
timeout=5

# Wait for window with this PID to appear
start_time=$(date +%s)
while true; do
    if [ $(($(date +%s) - start_time)) -gt $timeout ]; then
        echo "Timeout waiting for window" >&2
        exit 1
    fi

    window_id=$(niri msg --json windows | jq -r ".[] | select(.pid == $pid) | .id")
    if [ -n "$window_id" ]; then
        break
    fi

    sleep 0.1
done

# Apply workspace if specified
if [ -n "$workspace" ]; then
    niri msg action move-window-to-workspace --window-id "$window_id" --focus false "$workspace"
fi

# Apply width if specified
if [ -n "$width" ]; then
    niri msg action set-window-width --id "$window_id" "${width}%"
fi
```

---

## Deployment Steps

1. **Create the 4 scripts** in appropriate directories:
   - `bin/tmux/wlr-tmux-niri-tracker`
   - `bin/tmux/wlr-tmux-resurrect-save-workspaces`
   - `bin/tmux/wlr-tmux-resurrect-restore-workspaces`
   - `bin/wayland/wlr-niri-configure-window`

2. **Update `.tmux.conf`** with resurrect hooks:
   ```bash
   set -g @resurrect-hook-post-save-layout 'wlr-tmux-resurrect-save-workspaces'
   set -g @resurrect-hook-pre-restore-pane-processes 'wlr-tmux-resurrect-restore-workspaces'
   ```

3. **Enable systemd timer for automatic tracking:**

   The repository includes systemd user service/timer files that automatically run the tracker every 30 seconds. The service checks for `NIRI_SOCKET` environment variable, so **individual runs skip on X11 machines** or before niri has started.

   After syncing dotfiles (which symlinks the systemd files):
   ```bash
   # Reload systemd to pick up new timer
   systemctl --user daemon-reload

   # Enable and start the timer
   systemctl --user enable --now wlr-tmux-niri-tracker.timer

   # Check status
   systemctl --user status wlr-tmux-niri-tracker.timer
   systemctl --user list-timers wlr-tmux-niri-tracker.timer
   ```

   The timer runs on all machines, but each execution checks `ConditionEnvironment=NIRI_SOCKET` on the service unit:
   - On niri machines: Runs succeed once niri exports `NIRI_SOCKET`
   - On X11 machines: Each run skips harmlessly
   - After reboot: Early runs skip until niri starts and exports the variable

4. **Test workflow:**
   ```bash
   # Initial tracking
   wlr-tmux-niri-tracker

   # Verify options set
   tmux show-options -t <session> @niri-workspace
   tmux show-options -t <session> @niri-width

   # Force resurrect save
   tmux run-shell ~/.tmux/plugins/tmux-resurrect/scripts/save.sh

   # Check workspace and width lines in save file
   grep -E 'niri_(workspace|width)' ~/.local/share/tmux/resurrect/last

   # Kill and restore tmux
   tmux kill-server
   # Wait for continuum restore

   # Open sessions
   wlr-open-tmux-sessions
   ```

---

## Advanced: Event-Driven Tracking

For real-time accuracy, use niri event stream instead of polling:

**Alternative tracker using event stream:**
```bash
# Subscribe to niri events and update on WindowOpened/WindowClosed
niri msg event-stream | jq --unbuffered -r 'select(.WindowOpened or .WindowClosed)' | \
    while read -r event; do
        wlr-tmux-niri-tracker
    done
```

Run as background systemd service.

---

## Key Design Decisions

1. **Storage:** tmux user options (@niri-workspace, @niri-width) - ephemeral but restored by hooks
2. **Persistence:** Custom line types in resurrect save file (niri_workspace, niri_width)
3. **Tracking:** PID-based linking (alacritty → tmux client → session)
4. **Restoration:** niri IPC to spawn windows, move to workspace, and set width
5. **Timing:** Tracker runs periodically or on events; restore after continuum finishes
6. **Width Precision:** Rounded to nearest 10% of screen width for consistency

---

## Technical Details

### niri IPC

- **Socket:** `$NIRI_SOCKET` environment variable
- **CLI:** `niri msg` command for querying and actions
- **Query windows:** `niri msg --json windows`
- **Query workspaces:** `niri msg --json workspaces`
- **Move window:** `niri msg action move-window-to-workspace ...`

### tmux-resurrect Format

Save file location: `~/.local/share/tmux/resurrect/last`

Tab-delimited format with line types:
- `pane\t<session>\t<window>\t...`
- `window\t<session>\t...`
- `state\t<client_session>`

Custom line types for this solution:
- `niri_workspace\t<session>\t<workspace_id>`
- `niri_width\t<session>\t<width_percent>`

### Process Relationships

```
niri (compositor)
└─ alacritty (PID known to niri)
   └─ tmux attach-session -t <session> (child process)
      → Connected to tmux server
```

To find session from alacritty PID:
1. Get alacritty children: `pgrep -P <alacritty_pid>`
2. Check if child is tmux: `ps -o comm= -p <child_pid>`
3. Parse session: `ps -o args= -p <child_pid>` → extract `-t <session>`
