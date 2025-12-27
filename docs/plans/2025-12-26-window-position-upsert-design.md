# Window Position Upsert Design

## Overview

Redesigns window position storage to use upsert semantics with boot-keyed data, solving the problem where apps restoring at different times clobber each other's position data.

## Problem

Current storage uses full-replacement semantics:
- `track_terminals()` saves ordering for tmux/mosh, replacing any librewolf entries
- `librewolf_host.handle_store()` saves ordering for librewolf, replacing terminal entries
- During restoration, if app A restores before app B, a tracking run can clobber B's position data before B restores

Additionally, cross-app ordering is fundamentally broken because each tracker only knows its own stable IDs but writes to shared storage.

## Solution

Store position data keyed by boot session, with entries containing both stable IDs and ephemeral window IDs. Each tracker upserts only its known entries. Cross-app ordering is recovered by looking up predecessor relationships from historical boots where both apps were tracked.

## Storage Schema

**File:** `~/.local/state/niri/positions.json`

```json
{
  "version": 1,
  "boots": {
    "a1b2c3d4-e5f6-7890-abcd-ef1234567890": {
      "updated_at": "2025-12-26T10:30:00Z",
      "apps": ["tmux", "mosh", "librewolf"],
      "workspaces": {
        "1": [
          {"id": "tmux:dotfiles", "index": 1, "window_id": 100, "width": 50},
          {"id": "librewolf:uuid-abc", "index": 2, "window_id": 200, "width": 40},
          {"id": "mosh:server:main", "index": 3, "window_id": 300, "width": 50}
        ]
      }
    }
  }
}
```

**Fields:**
- `boots.<id>`: UUID identifying a boot session
- `boots.<id>.updated_at`: Last time any tracker wrote to this boot
- `boots.<id>.apps`: Set of app namespaces that have written to this boot
- `boots.<id>.workspaces.<ws>`: List of position entries per workspace
- Entry fields: `id` (stable ID), `index` (column position), `window_id` (niri window ID), `width` (percentage)

**Boot ID source:** `/run/user/$UID/niri-tracker-boot`
- If missing: generate UUID, write to file
- If present: read and use

## Tracking Algorithm

When a tracker runs (e.g., `track_terminals` or `librewolf_host.handle_store`):

```
1. ACQUIRE LOCK on positions.json

2. GET BOOT ID
   - Read /run/user/$UID/niri-tracker-boot
   - If missing: generate UUID, write to file

3. LOAD positions.json

4. COLLECT POSITIONS
   For each window this tracker can identify:
     - Observe: stable_id, column_index, niri_window_id, workspace_id, width
     - Build entry: {id, index, window_id, width}

5. UPSERT INTO CURRENT BOOT
   - Ensure boots[boot_id] exists with updated_at, apps=[], workspaces={}
   - Add this app namespace to boots[boot_id].apps (if not present)
   - For each workspace with entries:
     - Remove any existing entries with same stable_id (handles workspace moves)
     - Append new entries
   - Update boots[boot_id].updated_at to now

6. PRUNE DOMINATED BOOTS
   For each other boot B:
     If current_boot.apps ⊇ B.apps AND current_boot.updated_at > B.updated_at:
       Delete B

7. SAVE positions.json

8. RELEASE LOCK
```

## Restoration Algorithm

When restoring windows for an app:

```
1. ACQUIRE LOCK on positions.json
2. LOAD positions.json
3. GET BOOT ID (from /run/user/$UID/niri-tracker-boot, create if missing)

4. FOR EACH WINDOW TO RESTORE:

   4a. SPAWN WINDOW
       - Start the process (e.g., terminal with tmux attach)
       - Wait for niri window to appear, get window_id

   4b. DETERMINE TARGET WORKSPACE
       - Find most recent boot containing this stable_id
       - Get workspace_id and width from that entry
       - Move window to target workspace, apply saved width

   4c. FIND PREDECESSORS
       predecessors = []
       For each app namespace other_app in {tmux, mosh, librewolf}:
         Find newest boot B where:
           - B.apps contains both this_app and other_app
           - B has an entry for this_id in target workspace
         If found:
           this_index = index of this_id in B
           For each entry E in same workspace of B:
             If E.index < this_index:
               predecessors.append(E.id)

   4d. RESOLVE TO WINDOW IDS
       resolved = []
       For each predecessor stable_id:
         If found in current boot's workspace data:
           resolved.append(its window_id)

   4e. POSITION WINDOW
       Find spacer window(s) in target workspace
       candidates = resolved + spacer_window_ids
       If candidates is empty:
         Position at column 1
       Else:
         Find rightmost candidate by current column position
         Position this window immediately to its right

   4f. RECORD NEW POSITION (in-memory)
       - Remove any existing entries for this stable_id (any workspace)
       - Add {id, index, window_id, width} to current boot's workspace data
       - Add this app to current boot's apps set
       - Update current boot's updated_at

5. PRUNE DOMINATED BOOTS
6. SAVE positions.json
7. RELEASE LOCK
```

## Locking

Use `fcntl.flock` with exclusive lock on positions.json:

```python
import fcntl

def with_positions_lock(func):
    path = STATE_DIR / "positions.json"
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "a+") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            return func()
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
```

## Dominance-Based Expiration

Boot A dominates Boot B if:
1. A.apps ⊇ B.apps (A has all apps that B has)
2. A.updated_at > B.updated_at (A is newer)

After each tracking/restore operation updates a boot, dominated boots are pruned. This naturally preserves boots needed for cross-app relationship lookups while removing redundant ones.

**Example:**
- Boot X: {tmux, mosh} @ T1
- Boot Y: {librewolf} @ T2
- Boot Z (current): {tmux, mosh, librewolf} @ T3

After Z updates: Z dominates both X and Y → both pruned.

Later, Boot W: {tmux} @ T4. W doesn't dominate Z (missing mosh, librewolf) → Z preserved.

## Storage Consolidation

**After this change:**
- `positions.json` — all workspace/width/ordering data
- `librewolf-identities.json` — URL→UUID mappings (unchanged)

**Deprecated (delete manually):**
- `tmux.json`, `mosh.json`, `librewolf.json` — workspace/width moved to positions.json
- `orders.json` — ordering moved to positions.json

## Code Changes

**New module:** `src/wlrenv/niri/positions.py`
- Boot ID management
- Load/save with locking
- Upsert entries
- Dominance pruning
- Predecessor lookup

**Modify:** `src/wlrenv/niri/track.py`
- Use positions.upsert_entries() instead of storage + order_storage

**Modify:** `src/wlrenv/niri/librewolf_host.py`
- Same changes for handle_store() and handle_restore()

**Modify:** `src/wlrenv/niri/restore.py`
- Use positions.py for lookups and predecessor resolution

**Delete:** `src/wlrenv/niri/storage.py`, `src/wlrenv/niri/order_storage.py`

## Edge Cases

1. **First boot:** No predecessors → position after spacer
2. **App not in recent boots:** Lookup falls back to older boots
3. **Window deleted:** Entry remains until boot is dominated
4. **Corrupted positions.json:** Start fresh, graceful degradation
5. **Race between trackers:** Lock ensures serialization
6. **Boot ID file deleted:** New UUID generated, self-corrects

## Testing Strategy

- Unit tests: upsert, predecessor lookup, dominance pruning
- Integration tests: tracking → restore round-trip with mock IPC
- Edge case tests: empty state, single app, cross-app ordering, workspace moves
