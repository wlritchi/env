#!/usr/bin/env bash
# Common library functions for niri workspace tracking
# Source this file in scripts that need niri IPC functionality

# Check if niri is available and NIRI_SOCKET is set
# Returns 0 if ready, 1 if not
wlr_niri_check_available() {
    if ! command -v niri >/dev/null 2>&1; then
        echo "niri not found" >&2
        return 1
    fi

    if ! command -v jq >/dev/null 2>&1; then
        echo "jq not found" >&2
        return 1
    fi

    if [ -z "${NIRI_SOCKET:-}" ]; then
        echo "NIRI_SOCKET not set (not running under niri?)" >&2
        return 1
    fi

    return 0
}

# Get all windows from niri as JSON
# Usage: wlr_niri_get_windows
wlr_niri_get_windows() {
    niri msg --json windows
}

# Get window ID by PID
# Usage: window_id=$(wlr_niri_get_window_id_by_pid <pid>)
# Returns empty string if not found
wlr_niri_get_window_id_by_pid() {
    local pid="$1"
    wlr_niri_get_windows | jq -r ".[] | select(.pid == $pid) | .id" | head -1
}

# Get workspace ID by window ID
# Usage: workspace_id=$(wlr_niri_get_workspace_by_window_id <window_id>)
wlr_niri_get_workspace_by_window_id() {
    local window_id="$1"
    wlr_niri_get_windows | jq -r ".[] | select(.id == $window_id) | .workspace_id"
}

# Move window to workspace
# Usage: wlr_niri_move_window <window_id> <workspace_index> [focus_bool]
#   focus_bool: optional, "true" or "false" (default: false)
wlr_niri_move_window() {
    local window_id="$1"
    local workspace_index="$2"
    local focus="${3:-false}"

    niri msg action move-window-to-workspace --window-id "$window_id" --focus "$focus" "$workspace_index"
}

# Wait for window with specific PID to appear, with timeout
# Usage: window_id=$(wlr_niri_wait_for_window_by_pid <pid> [timeout_seconds])
# Returns window ID on success, exits with error on timeout
wlr_niri_wait_for_window_by_pid() {
    local pid="$1"
    local timeout="${2:-5}"
    local start_time window_id

    start_time=$(date +%s)

    while true; do
        if [ $(($(date +%s) - start_time)) -gt $timeout ]; then
            echo "Timeout waiting for window with PID $pid" >&2
            return 1
        fi

        window_id=$(wlr_niri_get_window_id_by_pid "$pid")
        if [ -n "$window_id" ]; then
            echo "$window_id"
            return 0
        fi

        sleep 0.1
    done
}

# Get all windows for a specific app_id
# Usage: wlr_niri_get_windows_by_app_id <app_id>
# Returns JSON array of matching windows
wlr_niri_get_windows_by_app_id() {
    local app_id="$1"
    wlr_niri_get_windows | jq "[.[] | select(.app_id == \"$app_id\")]"
}

# Pretty-print a window (for debugging/logging)
# Usage: wlr_niri_print_window <window_json>
wlr_niri_print_window() {
    local window="$1"
    local id title workspace_id pid

    id=$(echo "$window" | jq -r '.id')
    title=$(echo "$window" | jq -r '.title')
    workspace_id=$(echo "$window" | jq -r '.workspace_id')
    pid=$(echo "$window" | jq -r '.pid')

    echo "Window $id (PID $pid) on workspace $workspace_id: \"$title\""
}
