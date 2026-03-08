# wlr-qmk-flash Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Create a `wlr-qmk-flash` script that fetches the user's QMK fork, detects the connected Moonlander revision, and flashes firmware.

**Architecture:** Single bash script with three phases: USB detection (platform-conditional), repo management (clone/pull), and build+flash (delegates to `qmk flash`).

**Tech Stack:** Bash, lsusb (Linux), system_profiler (macOS), git, qmk CLI

**Design doc:** `docs/plans/2026-03-07-qmk-flash-design.md`

---

### Task 1: Create the script with constants and helpers

**Files:**
- Create: `bin/util/wlr-qmk-flash`

**Step 1: Write the script skeleton**

```bash
#!/usr/bin/env bash
set -euo pipefail

FORK_URL="https://github.com/wlritchi/qmk_firmware"
QMK_DIR="$HOME/qmk_firmware"
KEYMAP="wlr-dvorakish"

# VID:PID → revision mapping
# Normal mode:  3297:1969 = reva, 3297:1972 = revb
# DFU mode:     0483:df11 = reva, 3297:2003 = revb

err() {
    printf '\033[1;31m✗\033[0m %s\n' "$1" >&2
}

info() {
    printf '\033[1;34m→\033[0m %s\n' "$1"
}

ok() {
    printf '\033[1;32m✓\033[0m %s\n' "$1"
}
```

**Step 2: Make executable**

```bash
chmod +x bin/util/wlr-qmk-flash
```

**Step 3: Verify it runs**

Run: `bin/util/wlr-qmk-flash`
Expected: Exits 0, no output (script has no main logic yet)

---

### Task 2: Add USB detection function

**Files:**
- Modify: `bin/util/wlr-qmk-flash`

**Step 1: Add the detect_revision function**

Append after the helpers:

```bash
detect_revision() {
    local reva=false
    local revb=false

    case "$(uname)" in
        Linux)
            if lsusb -d 3297:1969 >/dev/null 2>&1 || lsusb -d 0483:df11 >/dev/null 2>&1; then
                reva=true
            fi
            if lsusb -d 3297:1972 >/dev/null 2>&1 || lsusb -d 3297:2003 >/dev/null 2>&1; then
                revb=true
            fi
            ;;
        Darwin)
            local usb_info
            usb_info="$(system_profiler SPUSBDataType 2>/dev/null)"
            # Check for Rev A (normal: 3297/1969, DFU: 0483/df11)
            if echo "$usb_info" | grep -q '0x3297' && echo "$usb_info" | grep -q '0x1969'; then
                reva=true
            fi
            if echo "$usb_info" | grep -q '0x0483' && echo "$usb_info" | grep -q '0xdf11'; then
                reva=true
            fi
            # Check for Rev B (normal: 3297/1972, DFU: 3297/2003)
            if echo "$usb_info" | grep -q '0x3297' && echo "$usb_info" | grep -q '0x1972'; then
                revb=true
            fi
            if echo "$usb_info" | grep -q '0x3297' && echo "$usb_info" | grep -q '0x2003'; then
                revb=true
            fi
            ;;
        *)
            err "Unsupported platform: $(uname)"
            exit 1
            ;;
    esac

    if $reva && $revb; then
        err "Both Rev A and Rev B detected — disconnect one keyboard and retry"
        exit 1
    elif $reva; then
        echo "reva"
    elif $revb; then
        echo "revb"
    else
        err "No Moonlander detected — connect your keyboard and retry"
        exit 1
    fi
}
```

**Note on macOS detection:** `system_profiler SPUSBDataType` outputs a tree structure where Vendor ID and Product ID appear on adjacent lines under each device entry. The grep approach checks for the presence of both IDs anywhere in the output. This could theoretically false-positive if two unrelated devices happen to have matching vendor and product IDs separately, but in practice the ZSA vendor ID (0x3297) is unique enough that this is not a concern. The STM DFU vendor ID (0x0483) is more common, but `0xdf11` as a product ID is specific to STM32 DFU — and even a false positive there just means the script would attempt to flash Rev A, which `qmk flash` would fail gracefully on if the wrong device is connected.

---

### Task 3: Add repo management function

**Files:**
- Modify: `bin/util/wlr-qmk-flash`

**Step 1: Add the ensure_repo function**

Append after detect_revision:

```bash
ensure_repo() {
    if [[ ! -d "$QMK_DIR" ]]; then
        info "Cloning QMK firmware fork..."
        git clone "$FORK_URL" "$QMK_DIR"
    else
        info "Updating QMK firmware..."
        if ! git -C "$QMK_DIR" pull --ff-only 2>/dev/null; then
            err "Failed to pull (local changes or diverged history) — building with current state"
        fi
    fi
}
```

---

### Task 4: Add main flow

**Files:**
- Modify: `bin/util/wlr-qmk-flash`

**Step 1: Add the main logic**

Append at the end of the script:

```bash
# Check for qmk
if ! command -v qmk >/dev/null 2>&1; then
    err "qmk not found on PATH — check your Nix setup"
    exit 1
fi

# Detect board revision
info "Detecting connected Moonlander revision..."
rev="$(detect_revision)"
ok "Detected Moonlander $rev"

# Fetch/update repo
ensure_repo

# Flash
info "Compiling and flashing zsa/moonlander/$rev:$KEYMAP..."
cd "$QMK_DIR"
qmk flash -kb "zsa/moonlander/$rev" -km "$KEYMAP"
ok "Successfully flashed Moonlander $rev"
```

**Step 2: Verify script syntax**

Run: `bash -n bin/util/wlr-qmk-flash`
Expected: No output (no syntax errors)

---

### Task 5: Commit

**Step 1: Stage and commit**

```bash
git add bin/util/wlr-qmk-flash
git commit -m "feat: add wlr-qmk-flash for Moonlander firmware updates"
```
