# wlr-qmk-flash Design

## Overview

A single bash script at `bin/util/wlr-qmk-flash` that fetches the user's QMK fork, detects which Moonlander board revision is connected, and compiles+flashes the firmware.

## Script Location & Structure

- **File:** `bin/util/wlr-qmk-flash`
- Standard `#!/usr/bin/env bash` + `set -euo pipefail`
- Inline color output for errors/success (no `wlr-err`/`wlr-good` dependency)
- No required arguments; fully automatic

**Constants:**
- Fork URL: `https://github.com/wlritchi/qmk_firmware`
- QMK dir: `$HOME/qmk_firmware`
- Keymap: `wlr-dvorakish`

## USB Detection

Platform-conditional detection checking 4 known VID:PID pairs:

| VID:PID       | Mode   | Revision |
|---------------|--------|----------|
| `3297:1969`   | Normal | Rev A    |
| `3297:1972`   | Normal | Rev B    |
| `0483:df11`   | DFU    | Rev A    |
| `3297:2003`   | DFU    | Rev B    |

- **Linux:** Parse `lsusb` output for each VID:PID
- **macOS:** Parse `system_profiler SPUSBDataType` for vendor/product ID pairs

**Logic:**
- Exactly one revision detected → use it
- No known device found → error asking user to connect keyboard
- Both revisions detected → error explaining ambiguity

## Repository Management

1. If `$HOME/qmk_firmware` doesn't exist → `git clone <fork_url> "$HOME/qmk_firmware"`
2. If it exists → `git -C "$HOME/qmk_firmware" pull --ff-only`
   - If pull fails (local changes, diverged history) → warn but continue with current state

No branch management — pulls whatever branch is currently checked out.

## Build & Flash

1. Run `qmk flash -kb zsa/moonlander/<detected_rev> -km wlr-dvorakish` from `$HOME/qmk_firmware`
2. `qmk flash` handles compilation, DFU mode prompting, and flashing
3. If `qmk` not on PATH → error with message to check Nix setup
4. On success → print confirmation with the revision that was flashed
