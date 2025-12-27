# Window Position Upsert Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace window position storage with boot-keyed upsert semantics to prevent cross-app clobbering during restoration.

**Architecture:** New `positions.py` module handles all position data with file locking. Tracking and restoration use upsert operations keyed by boot UUID. Cross-app ordering recovered via predecessor lookup across historical boots. Dominance-based pruning expires redundant boots.

**Tech Stack:** Python 3.12+, fcntl for file locking, pytest for testing, existing niri IPC layer.

**Design doc:** `docs/plans/2025-12-26-window-position-upsert-design.md`

---

## Task 1: Create positions.py with Boot ID Management

**Files:**
- Create: `src/wlrenv/niri/positions.py`
- Create: `tests/niri/test_positions.py`

**Step 1: Write failing test for boot ID creation**

```python
# tests/niri/test_positions.py
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def temp_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Set up temp state and run directories."""
    from wlrenv.niri import config

    state_dir = tmp_path / "state"
    run_dir = tmp_path / "run"
    state_dir.mkdir()
    run_dir.mkdir()

    monkeypatch.setattr(config, "STATE_DIR", state_dir)
    monkeypatch.setattr("wlrenv.niri.positions._get_run_dir", lambda: run_dir)
    return state_dir, run_dir


def test_get_boot_id_creates_new_uuid(temp_dirs: tuple[Path, Path]) -> None:
    from wlrenv.niri.positions import get_boot_id

    state_dir, run_dir = temp_dirs
    boot_file = run_dir / "niri-tracker-boot"

    assert not boot_file.exists()

    boot_id = get_boot_id()

    assert boot_file.exists()
    assert len(boot_id) == 36  # UUID format
    assert boot_id == boot_file.read_text().strip()


def test_get_boot_id_returns_existing(temp_dirs: tuple[Path, Path]) -> None:
    from wlrenv.niri.positions import get_boot_id

    state_dir, run_dir = temp_dirs
    boot_file = run_dir / "niri-tracker-boot"
    boot_file.write_text("existing-boot-uuid")

    boot_id = get_boot_id()

    assert boot_id == "existing-boot-uuid"
```

**Step 2: Run test to verify it fails**

Run: `cd /home/wlritchi/.wlrenv/.worktrees/position-upsert && uv run pytest tests/niri/test_positions.py -v`
Expected: FAIL with "ModuleNotFoundError: No module named 'wlrenv.niri.positions'"

**Step 3: Write minimal implementation**

```python
# src/wlrenv/niri/positions.py
"""Boot-keyed window position storage with upsert semantics."""

from __future__ import annotations

import os
import uuid as uuid_lib
from pathlib import Path


def _get_run_dir() -> Path:
    """Get XDG runtime directory."""
    xdg_runtime = os.environ.get("XDG_RUNTIME_DIR")
    if xdg_runtime:
        return Path(xdg_runtime)
    return Path(f"/run/user/{os.getuid()}")


def get_boot_id() -> str:
    """Get or create boot ID for current session."""
    run_dir = _get_run_dir()
    boot_file = run_dir / "niri-tracker-boot"

    if boot_file.exists():
        return boot_file.read_text().strip()

    boot_id = str(uuid_lib.uuid4())
    run_dir.mkdir(parents=True, exist_ok=True)
    boot_file.write_text(boot_id)
    return boot_id
```

**Step 4: Run test to verify it passes**

Run: `cd /home/wlritchi/.wlrenv/.worktrees/position-upsert && uv run pytest tests/niri/test_positions.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/wlrenv/niri/positions.py tests/niri/test_positions.py
git commit -m "feat(positions): add boot ID management"
```

---

## Task 2: Add Load/Save with File Locking

**Files:**
- Modify: `src/wlrenv/niri/positions.py`
- Modify: `tests/niri/test_positions.py`

**Step 1: Write failing test for load/save**

Append to `tests/niri/test_positions.py`:

```python
def test_load_returns_empty_structure_for_missing_file(
    temp_dirs: tuple[Path, Path],
) -> None:
    from wlrenv.niri.positions import load_positions

    data = load_positions()

    assert data == {"version": 1, "boots": {}}


def test_save_and_load_round_trip(temp_dirs: tuple[Path, Path]) -> None:
    from wlrenv.niri.positions import load_positions, save_positions

    data = {
        "version": 1,
        "boots": {
            "boot-123": {
                "updated_at": "2025-12-26T10:00:00Z",
                "apps": ["tmux"],
                "workspaces": {
                    "1": [{"id": "tmux:dotfiles", "index": 1, "window_id": 100, "width": 50}]
                },
            }
        },
    }

    save_positions(data)
    loaded = load_positions()

    assert loaded == data


def test_save_is_atomic(temp_dirs: tuple[Path, Path]) -> None:
    from wlrenv.niri.positions import load_positions, save_positions

    state_dir, _ = temp_dirs

    # Save initial data
    save_positions({"version": 1, "boots": {"a": {"apps": []}}})

    # Check no temp files left behind
    files = list(state_dir.glob("*.tmp"))
    assert files == []

    # File exists
    assert (state_dir / "positions.json").exists()
```

**Step 2: Run test to verify it fails**

Run: `cd /home/wlritchi/.wlrenv/.worktrees/position-upsert && uv run pytest tests/niri/test_positions.py::test_load_returns_empty_structure_for_missing_file -v`
Expected: FAIL with "cannot import name 'load_positions'"

**Step 3: Write implementation**

Add to `src/wlrenv/niri/positions.py`:

```python
import fcntl
import json
import tempfile
from typing import Any

from wlrenv.niri.config import STATE_DIR


def _get_positions_path() -> Path:
    """Get path to positions.json."""
    return STATE_DIR / "positions.json"


def load_positions() -> dict[str, Any]:
    """Load positions data, returning empty structure if missing."""
    path = _get_positions_path()
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {"version": 1, "boots": {}}


def save_positions(data: dict[str, Any]) -> None:
    """Atomically save positions data."""
    path = _get_positions_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    # Write to temp file, then atomic rename
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".json.tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.rename(tmp_path, path)
    except BaseException:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


class PositionsLock:
    """Context manager for exclusive access to positions.json."""

    def __init__(self) -> None:
        self._lock_path = STATE_DIR / "positions.lock"
        self._lock_file: Any = None

    def __enter__(self) -> "PositionsLock":
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self._lock_file = open(self._lock_path, "w")
        fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, *args: Any) -> None:
        if self._lock_file:
            fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
            self._lock_file.close()
```

**Step 4: Run tests to verify they pass**

Run: `cd /home/wlritchi/.wlrenv/.worktrees/position-upsert && uv run pytest tests/niri/test_positions.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/wlrenv/niri/positions.py tests/niri/test_positions.py
git commit -m "feat(positions): add load/save with file locking"
```

---

## Task 3: Add Upsert Entries Function

**Files:**
- Modify: `src/wlrenv/niri/positions.py`
- Modify: `tests/niri/test_positions.py`

**Step 1: Write failing test for upsert**

Append to `tests/niri/test_positions.py`:

```python
def test_upsert_entries_creates_boot(temp_dirs: tuple[Path, Path]) -> None:
    from wlrenv.niri.positions import load_positions, upsert_entries

    entries = [
        {"id": "tmux:dotfiles", "index": 1, "window_id": 100, "width": 50},
        {"id": "tmux:scratch", "index": 2, "window_id": 101, "width": 40},
    ]

    upsert_entries(app="tmux", workspace_id=1, entries=entries)

    data = load_positions()
    boot_id = list(data["boots"].keys())[0]
    boot = data["boots"][boot_id]

    assert "tmux" in boot["apps"]
    assert boot["workspaces"]["1"] == entries


def test_upsert_entries_merges_apps(temp_dirs: tuple[Path, Path]) -> None:
    from wlrenv.niri.positions import load_positions, upsert_entries

    upsert_entries(app="tmux", workspace_id=1, entries=[
        {"id": "tmux:a", "index": 1, "window_id": 100, "width": 50}
    ])
    upsert_entries(app="mosh", workspace_id=1, entries=[
        {"id": "mosh:b", "index": 2, "window_id": 200, "width": 50}
    ])

    data = load_positions()
    boot_id = list(data["boots"].keys())[0]
    boot = data["boots"][boot_id]

    assert set(boot["apps"]) == {"tmux", "mosh"}
    assert len(boot["workspaces"]["1"]) == 2


def test_upsert_entries_removes_stale_same_id(temp_dirs: tuple[Path, Path]) -> None:
    from wlrenv.niri.positions import load_positions, upsert_entries

    # First upsert: window on workspace 1
    upsert_entries(app="tmux", workspace_id=1, entries=[
        {"id": "tmux:a", "index": 1, "window_id": 100, "width": 50}
    ])

    # Second upsert: same window moved to workspace 2
    upsert_entries(app="tmux", workspace_id=2, entries=[
        {"id": "tmux:a", "index": 1, "window_id": 100, "width": 50}
    ])

    data = load_positions()
    boot_id = list(data["boots"].keys())[0]
    boot = data["boots"][boot_id]

    # Should only exist on workspace 2 now
    assert boot["workspaces"].get("1", []) == []
    assert len(boot["workspaces"]["2"]) == 1
```

**Step 2: Run test to verify it fails**

Run: `cd /home/wlritchi/.wlrenv/.worktrees/position-upsert && uv run pytest tests/niri/test_positions.py::test_upsert_entries_creates_boot -v`
Expected: FAIL with "cannot import name 'upsert_entries'"

**Step 3: Write implementation**

Add to `src/wlrenv/niri/positions.py`:

```python
from datetime import datetime, timezone


def upsert_entries(
    app: str,
    workspace_id: int,
    entries: list[dict[str, Any]],
) -> None:
    """Upsert position entries for an app into current boot."""
    with PositionsLock():
        data = load_positions()
        boot_id = get_boot_id()
        ws_key = str(workspace_id)

        # Ensure boot exists
        if boot_id not in data["boots"]:
            data["boots"][boot_id] = {
                "updated_at": "",
                "apps": [],
                "workspaces": {},
            }

        boot = data["boots"][boot_id]

        # Add app to set
        if app not in boot["apps"]:
            boot["apps"].append(app)

        # Remove any existing entries with same stable IDs (from any workspace)
        entry_ids = {e["id"] for e in entries}
        for ws, ws_entries in boot["workspaces"].items():
            boot["workspaces"][ws] = [e for e in ws_entries if e["id"] not in entry_ids]

        # Clean up empty workspaces
        boot["workspaces"] = {k: v for k, v in boot["workspaces"].items() if v}

        # Add new entries
        if ws_key not in boot["workspaces"]:
            boot["workspaces"][ws_key] = []
        boot["workspaces"][ws_key].extend(entries)

        # Update timestamp
        boot["updated_at"] = datetime.now(timezone.utc).isoformat()

        save_positions(data)
```

**Step 4: Run tests to verify they pass**

Run: `cd /home/wlritchi/.wlrenv/.worktrees/position-upsert && uv run pytest tests/niri/test_positions.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/wlrenv/niri/positions.py tests/niri/test_positions.py
git commit -m "feat(positions): add upsert_entries function"
```

---

## Task 4: Add Dominance-Based Pruning

**Files:**
- Modify: `src/wlrenv/niri/positions.py`
- Modify: `tests/niri/test_positions.py`

**Step 1: Write failing test for pruning**

Append to `tests/niri/test_positions.py`:

```python
def test_prune_dominated_boots(temp_dirs: tuple[Path, Path]) -> None:
    from wlrenv.niri.positions import load_positions, prune_dominated_boots, save_positions

    data = {
        "version": 1,
        "boots": {
            "old": {
                "updated_at": "2025-12-25T10:00:00Z",
                "apps": ["tmux"],
                "workspaces": {},
            },
            "current": {
                "updated_at": "2025-12-26T10:00:00Z",
                "apps": ["tmux", "mosh"],
                "workspaces": {},
            },
        },
    }
    save_positions(data)

    prune_dominated_boots("current")

    result = load_positions()
    # "old" should be pruned: "current" has superset of apps and is newer
    assert "old" not in result["boots"]
    assert "current" in result["boots"]


def test_prune_preserves_non_dominated(temp_dirs: tuple[Path, Path]) -> None:
    from wlrenv.niri.positions import load_positions, prune_dominated_boots, save_positions

    data = {
        "version": 1,
        "boots": {
            "has_librewolf": {
                "updated_at": "2025-12-25T10:00:00Z",
                "apps": ["librewolf"],
                "workspaces": {},
            },
            "current": {
                "updated_at": "2025-12-26T10:00:00Z",
                "apps": ["tmux"],
                "workspaces": {},
            },
        },
    }
    save_positions(data)

    prune_dominated_boots("current")

    result = load_positions()
    # "has_librewolf" preserved: "current" doesn't have librewolf
    assert "has_librewolf" in result["boots"]
    assert "current" in result["boots"]
```

**Step 2: Run test to verify it fails**

Run: `cd /home/wlritchi/.wlrenv/.worktrees/position-upsert && uv run pytest tests/niri/test_positions.py::test_prune_dominated_boots -v`
Expected: FAIL with "cannot import name 'prune_dominated_boots'"

**Step 3: Write implementation**

Add to `src/wlrenv/niri/positions.py`:

```python
def prune_dominated_boots(current_boot_id: str) -> None:
    """Remove boots dominated by the current boot.

    Boot A dominates Boot B if:
    1. A.apps is a superset of B.apps
    2. A.updated_at > B.updated_at
    """
    with PositionsLock():
        data = load_positions()

        if current_boot_id not in data["boots"]:
            return

        current = data["boots"][current_boot_id]
        current_apps = set(current["apps"])
        current_time = current["updated_at"]

        to_delete = []
        for boot_id, boot in data["boots"].items():
            if boot_id == current_boot_id:
                continue
            boot_apps = set(boot["apps"])
            # Current dominates boot if current has all of boot's apps and is newer
            if boot_apps <= current_apps and boot["updated_at"] < current_time:
                to_delete.append(boot_id)

        for boot_id in to_delete:
            del data["boots"][boot_id]

        if to_delete:
            save_positions(data)
```

**Step 4: Run tests to verify they pass**

Run: `cd /home/wlritchi/.wlrenv/.worktrees/position-upsert && uv run pytest tests/niri/test_positions.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/wlrenv/niri/positions.py tests/niri/test_positions.py
git commit -m "feat(positions): add dominance-based boot pruning"
```

---

## Task 5: Add Lookup Functions for Restoration

**Files:**
- Modify: `src/wlrenv/niri/positions.py`
- Modify: `tests/niri/test_positions.py`

**Step 1: Write failing tests for lookups**

Append to `tests/niri/test_positions.py`:

```python
def test_lookup_latest_position(temp_dirs: tuple[Path, Path]) -> None:
    from wlrenv.niri.positions import lookup_latest_position, save_positions

    data = {
        "version": 1,
        "boots": {
            "old": {
                "updated_at": "2025-12-25T10:00:00Z",
                "apps": ["tmux"],
                "workspaces": {
                    "1": [{"id": "tmux:a", "index": 1, "window_id": 100, "width": 50}]
                },
            },
            "new": {
                "updated_at": "2025-12-26T10:00:00Z",
                "apps": ["tmux"],
                "workspaces": {
                    "2": [{"id": "tmux:a", "index": 2, "window_id": 200, "width": 60}]
                },
            },
        },
    }
    save_positions(data)

    result = lookup_latest_position("tmux:a")

    # Should return from newer boot
    assert result is not None
    assert result["workspace_id"] == 2
    assert result["width"] == 60


def test_lookup_latest_position_returns_none_for_unknown(
    temp_dirs: tuple[Path, Path],
) -> None:
    from wlrenv.niri.positions import lookup_latest_position

    result = lookup_latest_position("tmux:nonexistent")

    assert result is None


def test_find_predecessors_cross_app(temp_dirs: tuple[Path, Path]) -> None:
    from wlrenv.niri.positions import find_predecessors, save_positions

    data = {
        "version": 1,
        "boots": {
            "boot1": {
                "updated_at": "2025-12-26T10:00:00Z",
                "apps": ["tmux", "librewolf"],
                "workspaces": {
                    "1": [
                        {"id": "tmux:a", "index": 1, "window_id": 100, "width": 50},
                        {"id": "librewolf:x", "index": 2, "window_id": 200, "width": 40},
                        {"id": "tmux:b", "index": 3, "window_id": 300, "width": 50},
                    ]
                },
            },
        },
    }
    save_positions(data)

    # librewolf:x has predecessor tmux:a (index 1 < 2)
    predecessors = find_predecessors(
        stable_id="librewolf:x",
        this_app="librewolf",
        workspace_id=1,
    )

    assert "tmux:a" in predecessors
    assert "tmux:b" not in predecessors  # index 3 > 2
    assert "librewolf:x" not in predecessors  # self


def test_resolve_predecessors_to_window_ids(temp_dirs: tuple[Path, Path]) -> None:
    from wlrenv.niri.positions import (
        resolve_predecessors_to_window_ids,
        save_positions,
        get_boot_id,
    )

    boot_id = get_boot_id()
    data = {
        "version": 1,
        "boots": {
            boot_id: {
                "updated_at": "2025-12-26T10:00:00Z",
                "apps": ["tmux"],
                "workspaces": {
                    "1": [
                        {"id": "tmux:a", "index": 1, "window_id": 100, "width": 50},
                        {"id": "tmux:b", "index": 2, "window_id": 200, "width": 50},
                    ]
                },
            },
        },
    }
    save_positions(data)

    window_ids = resolve_predecessors_to_window_ids(
        predecessor_ids=["tmux:a", "tmux:missing"],
        workspace_id=1,
    )

    assert window_ids == [100]  # tmux:missing not found
```

**Step 2: Run test to verify it fails**

Run: `cd /home/wlritchi/.wlrenv/.worktrees/position-upsert && uv run pytest tests/niri/test_positions.py::test_lookup_latest_position -v`
Expected: FAIL with "cannot import name 'lookup_latest_position'"

**Step 3: Write implementation**

Add to `src/wlrenv/niri/positions.py`:

```python
def lookup_latest_position(stable_id: str) -> dict[str, Any] | None:
    """Find the most recent position for a stable ID.

    Returns dict with workspace_id and width, or None if not found.
    """
    data = load_positions()

    latest_time: str | None = None
    latest_result: dict[str, Any] | None = None

    for boot_id, boot in data["boots"].items():
        for ws_key, entries in boot["workspaces"].items():
            for entry in entries:
                if entry["id"] == stable_id:
                    if latest_time is None or boot["updated_at"] > latest_time:
                        latest_time = boot["updated_at"]
                        latest_result = {
                            "workspace_id": int(ws_key),
                            "width": entry["width"],
                        }

    return latest_result


def find_predecessors(
    stable_id: str,
    this_app: str,
    workspace_id: int,
) -> list[str]:
    """Find predecessor stable IDs from historical boots.

    For each app namespace, finds the newest boot containing both this_app
    and that namespace, then collects entries with smaller index than stable_id.
    """
    data = load_positions()
    ws_key = str(workspace_id)
    all_apps = {"tmux", "mosh", "librewolf"}
    predecessors: set[str] = set()

    for other_app in all_apps:
        # Find newest boot with both apps and containing stable_id
        best_boot: dict[str, Any] | None = None
        best_time: str | None = None

        for boot_id, boot in data["boots"].items():
            if this_app not in boot["apps"] or other_app not in boot["apps"]:
                continue
            if ws_key not in boot["workspaces"]:
                continue

            # Check if stable_id exists in this workspace
            entries = boot["workspaces"][ws_key]
            if not any(e["id"] == stable_id for e in entries):
                continue

            if best_time is None or boot["updated_at"] > best_time:
                best_time = boot["updated_at"]
                best_boot = boot

        if best_boot is None:
            continue

        # Find this_id's index and collect predecessors
        entries = best_boot["workspaces"][ws_key]
        this_index: int | None = None
        for entry in entries:
            if entry["id"] == stable_id:
                this_index = entry["index"]
                break

        if this_index is not None:
            for entry in entries:
                if entry["index"] < this_index and entry["id"] != stable_id:
                    predecessors.add(entry["id"])

    return list(predecessors)


def resolve_predecessors_to_window_ids(
    predecessor_ids: list[str],
    workspace_id: int,
) -> list[int]:
    """Resolve predecessor stable IDs to window IDs in current boot."""
    data = load_positions()
    boot_id = get_boot_id()
    ws_key = str(workspace_id)

    if boot_id not in data["boots"]:
        return []

    boot = data["boots"][boot_id]
    if ws_key not in boot["workspaces"]:
        return []

    entries = boot["workspaces"][ws_key]
    id_to_window: dict[str, int] = {e["id"]: e["window_id"] for e in entries}

    return [id_to_window[pid] for pid in predecessor_ids if pid in id_to_window]
```

**Step 4: Run tests to verify they pass**

Run: `cd /home/wlritchi/.wlrenv/.worktrees/position-upsert && uv run pytest tests/niri/test_positions.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/wlrenv/niri/positions.py tests/niri/test_positions.py
git commit -m "feat(positions): add lookup and predecessor resolution"
```

---

## Task 6: Update track.py to Use positions.py

**Files:**
- Modify: `src/wlrenv/niri/track.py`
- Modify: `tests/niri/test_track.py`

**Step 1: Read existing test to understand interface**

Review `tests/niri/test_track.py` to understand current test structure.

**Step 2: Update track.py imports and implementation**

Replace the storage and order_storage imports and calls:

```python
# src/wlrenv/niri/track.py
"""Track terminal windows and store their metadata."""

from __future__ import annotations

import subprocess
from collections import defaultdict

from wlrenv.niri import ipc, positions
from wlrenv.niri.identify import ProcessInfo, identify_mosh, identify_tmux


def calculate_width_percent(tile_width: float, output_width: int) -> int:
    """Calculate width as percentage of output, rounded to nearest 10%."""
    pct = tile_width / output_width * 100
    return int((pct + 5) // 10 * 10)


def get_child_processes(pid: int) -> list[ProcessInfo]:
    """Get child processes of given PID."""
    try:
        result = subprocess.run(
            ["pgrep", "-P", str(pid)],
            capture_output=True,
            text=True,
        )
        child_pids = [int(p) for p in result.stdout.strip().split() if p]
    except (subprocess.CalledProcessError, ValueError):
        return []

    children = []
    for cpid in child_pids:
        try:
            comm_result = subprocess.run(
                ["ps", "-o", "comm=", "-p", str(cpid)],
                capture_output=True,
                text=True,
            )
            comm = comm_result.stdout.strip()

            args_result = subprocess.run(
                ["ps", "-o", "args=", "-p", str(cpid)],
                capture_output=True,
                text=True,
            )
            args = args_result.stdout.strip().split()

            if comm:
                children.append(ProcessInfo(comm=comm, args=args))
        except subprocess.CalledProcessError:
            continue

    return children


def track_terminals() -> None:
    """Track all terminal windows and store their workspace/width."""
    windows = ipc.get_windows(app_id="Alacritty")
    outputs = {o.name: o for o in ipc.get_outputs()}
    workspaces = {w.id: w for w in ipc.get_workspaces()}

    # Collect entries per workspace for each app
    tmux_entries: dict[int, list[dict]] = defaultdict(list)
    mosh_entries: dict[int, list[dict]] = defaultdict(list)

    for window in windows:
        ws = workspaces.get(window.workspace_id)
        if not ws:
            continue
        output = outputs.get(ws.output)
        if not output:
            continue

        width_percent = calculate_width_percent(window.tile_width, output.width)

        children = get_child_processes(window.pid)
        for child in children:
            if identity := identify_tmux(child):
                if window.column is not None:
                    tmux_entries[window.workspace_id].append({
                        "id": f"tmux:{identity}",
                        "index": window.column,
                        "window_id": window.id,
                        "width": width_percent,
                    })
                break
            if identity := identify_mosh(child):
                if window.column is not None:
                    mosh_entries[window.workspace_id].append({
                        "id": f"mosh:{identity}",
                        "index": window.column,
                        "window_id": window.id,
                        "width": width_percent,
                    })
                break

    # Upsert entries for each app/workspace
    for workspace_id, entries in tmux_entries.items():
        positions.upsert_entries(app="tmux", workspace_id=workspace_id, entries=entries)

    for workspace_id, entries in mosh_entries.items():
        positions.upsert_entries(app="mosh", workspace_id=workspace_id, entries=entries)

    # Prune dominated boots
    boot_id = positions.get_boot_id()
    positions.prune_dominated_boots(boot_id)
```

**Step 3: Update test to use new storage**

Update `tests/niri/test_track.py`:

```python
# tests/niri/test_track.py
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def temp_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Set up temp state and run directories."""
    from wlrenv.niri import config

    state_dir = tmp_path / "state"
    run_dir = tmp_path / "run"
    state_dir.mkdir()
    run_dir.mkdir()

    monkeypatch.setattr(config, "STATE_DIR", state_dir)
    monkeypatch.setattr("wlrenv.niri.positions._get_run_dir", lambda: run_dir)
    return state_dir, run_dir


def test_calculate_width_percent_rounds_to_10() -> None:
    from wlrenv.niri.track import calculate_width_percent

    assert calculate_width_percent(500, 1000) == 50
    assert calculate_width_percent(333, 1000) == 30
    assert calculate_width_percent(666, 1000) == 70
    assert calculate_width_percent(450, 1000) == 50


def test_track_terminals_stores_tmux_session(temp_dirs: tuple[Path, Path]) -> None:
    from wlrenv.niri import positions
    from wlrenv.niri.track import track_terminals

    mock_window = MagicMock()
    mock_window.id = 123
    mock_window.pid = 1000
    mock_window.workspace_id = 1
    mock_window.tile_width = 500
    mock_window.column = 2

    mock_output = MagicMock()
    mock_output.name = "eDP-1"
    mock_output.width = 1000

    mock_workspace = MagicMock()
    mock_workspace.id = 1
    mock_workspace.output = "eDP-1"

    with (
        patch("wlrenv.niri.track.ipc.get_windows", return_value=[mock_window]),
        patch("wlrenv.niri.track.ipc.get_outputs", return_value=[mock_output]),
        patch("wlrenv.niri.track.ipc.get_workspaces", return_value=[mock_workspace]),
        patch("wlrenv.niri.track.get_child_processes") as mock_children,
        patch("wlrenv.niri.track.identify_tmux", return_value="dotfiles"),
    ):
        mock_children.return_value = [MagicMock(comm="tmux", args=["tmux"])]
        track_terminals()

    data = positions.load_positions()
    boot_id = list(data["boots"].keys())[0]
    entries = data["boots"][boot_id]["workspaces"]["1"]

    assert len(entries) == 1
    assert entries[0]["id"] == "tmux:dotfiles"
    assert entries[0]["width"] == 50


def test_track_terminals_saves_column_order(temp_dirs: tuple[Path, Path]) -> None:
    from wlrenv.niri import positions
    from wlrenv.niri.track import track_terminals

    mock_windows = [
        MagicMock(id=1, pid=100, workspace_id=1, tile_width=500, column=3),
        MagicMock(id=2, pid=200, workspace_id=1, tile_width=500, column=1),
    ]

    mock_output = MagicMock(name="eDP-1", width=1000)
    mock_workspace = MagicMock(id=1, output="eDP-1")

    with (
        patch("wlrenv.niri.track.ipc.get_windows", return_value=mock_windows),
        patch("wlrenv.niri.track.ipc.get_outputs", return_value=[mock_output]),
        patch("wlrenv.niri.track.ipc.get_workspaces", return_value=[mock_workspace]),
        patch("wlrenv.niri.track.get_child_processes") as mock_children,
        patch("wlrenv.niri.track.identify_tmux", side_effect=["a", "b"]),
        patch("wlrenv.niri.track.identify_mosh", return_value=None),
    ):
        mock_children.return_value = [MagicMock(comm="tmux", args=["tmux"])]
        track_terminals()

    data = positions.load_positions()
    boot_id = list(data["boots"].keys())[0]
    entries = data["boots"][boot_id]["workspaces"]["1"]

    # Entries preserve column index from windows
    ids_with_index = [(e["id"], e["index"]) for e in entries]
    assert ("tmux:a", 3) in ids_with_index
    assert ("tmux:b", 1) in ids_with_index
```

**Step 4: Run tests to verify they pass**

Run: `cd /home/wlritchi/.wlrenv/.worktrees/position-upsert && uv run pytest tests/niri/test_track.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/wlrenv/niri/track.py tests/niri/test_track.py
git commit -m "refactor(track): use positions.py for storage"
```

---

## Task 7: Update restore.py to Use positions.py

**Files:**
- Modify: `src/wlrenv/niri/restore.py`
- Modify: `tests/niri/test_restore.py`

**Step 1: Update restore.py**

```python
# src/wlrenv/niri/restore.py
"""Restore terminal windows to their saved workspaces."""

from __future__ import annotations

import os
import subprocess

from wlrenv.niri import ipc, ordering, positions


def get_detached_tmux_sessions() -> list[str]:
    """Get list of detached tmux session names."""
    try:
        result = subprocess.run(
            [
                "tmux",
                "list-sessions",
                "-F",
                "#{session_name}",
                "-f",
                "#{?#{session_attached},0,1}",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return []
        return [s.strip() for s in result.stdout.strip().split("\n") if s.strip()]
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []


def read_moshen_sessions() -> list[tuple[str, str]]:
    """Read mosh sessions from moshen state file."""
    import base64
    from pathlib import Path

    try:
        state_dir = os.environ.get(
            "XDG_STATE_HOME", str(Path.home() / ".local" / "state")
        )
        sessions_file = Path(state_dir) / "moshen" / "sessions"

        if not sessions_file.exists():
            return []

        sessions = []
        for line in sessions_file.read_text().strip().split("\n"):
            if not line:
                continue
            parts = line.split(":")
            if len(parts) >= 2:
                host = base64.b64decode(parts[0]).decode().strip()
                session = base64.b64decode(parts[1]).decode().strip()
                sessions.append((host, session))

        return sessions
    except (OSError, ValueError, UnicodeDecodeError):
        return []


def spawn_terminal(args: list[str]) -> subprocess.Popen[bytes]:
    """Spawn a terminal with the given command."""
    terminal = os.environ.get("TERMINAL", "alacritty")
    return subprocess.Popen([terminal, "-e", *args])


def _get_window_column(window_id: int, workspace_id: int) -> int | None:
    """Get the current column of a window."""
    windows = ipc.get_windows()
    for w in windows:
        if w.id == window_id and w.workspace_id == workspace_id:
            return w.column
    return None


def _find_spacer_window_ids(workspace_id: int) -> list[int]:
    """Find spacer window IDs in workspace."""
    windows = ipc.get_windows()
    return [
        w.id for w in windows
        if w.workspace_id == workspace_id and w.title == "niri-spacer window"
    ]


def _position_window(
    window_id: int,
    stable_id: str,
    app: str,
    workspace_id: int,
) -> None:
    """Position window based on predecessors."""
    # Find predecessors from historical boots
    predecessor_ids = positions.find_predecessors(
        stable_id=stable_id,
        this_app=app,
        workspace_id=workspace_id,
    )

    # Resolve to window IDs in current boot
    predecessor_window_ids = positions.resolve_predecessors_to_window_ids(
        predecessor_ids=predecessor_ids,
        workspace_id=workspace_id,
    )

    # Add spacer windows
    spacer_ids = _find_spacer_window_ids(workspace_id)
    candidate_ids = predecessor_window_ids + spacer_ids

    if not candidate_ids:
        # No predecessors, position at column 1
        return

    # Find rightmost candidate
    current_column = _get_window_column(window_id, workspace_id)
    if current_column is None:
        return

    ordering.place_window(
        window_id=window_id,
        identity=stable_id,
        workspace_id=workspace_id,
        current_column=current_column,
    )


def restore_tmux() -> None:
    """Restore detached tmux sessions to their saved workspaces."""
    sessions = get_detached_tmux_sessions()

    # Collect entries to record after all windows are positioned
    recorded_entries: list[tuple[int, dict]] = []

    with positions.PositionsLock():
        data = positions.load_positions()
        boot_id = positions.get_boot_id()

        for session in sessions:
            # Skip IDE embedded terminal sessions
            if len(session) == 7 and all(c in "0123456789abcdef" for c in session):
                continue

            stable_id = f"tmux:{session}"
            props = positions.lookup_latest_position(stable_id)
            proc = spawn_terminal(["tmux", "attach-session", "-t", session])

            if props:
                window_id = ipc.wait_for_window(pid=proc.pid)
                if window_id:
                    workspace_id = props["workspace_id"]
                    ipc.configure(window_id, workspace=workspace_id, width=props["width"])

                    _position_window(window_id, stable_id, "tmux", workspace_id)

                    # Get current column for recording
                    column = _get_window_column(window_id, workspace_id)
                    if column is not None:
                        recorded_entries.append((workspace_id, {
                            "id": stable_id,
                            "index": column,
                            "window_id": window_id,
                            "width": props["width"],
                        }))

        # Record all entries
        entries_by_workspace: dict[int, list[dict]] = {}
        for ws_id, entry in recorded_entries:
            entries_by_workspace.setdefault(ws_id, []).append(entry)

        for ws_id, entries in entries_by_workspace.items():
            positions.upsert_entries(app="tmux", workspace_id=ws_id, entries=entries)

        positions.prune_dominated_boots(boot_id)


def restore_mosh() -> None:
    """Restore mosh sessions to their saved workspaces."""
    sessions = read_moshen_sessions()

    recorded_entries: list[tuple[int, dict]] = []

    with positions.PositionsLock():
        data = positions.load_positions()
        boot_id = positions.get_boot_id()

        for host, session_name in sessions:
            identity = f"{host}:{session_name}"
            stable_id = f"mosh:{identity}"
            props = positions.lookup_latest_position(stable_id)
            proc = spawn_terminal(
                [
                    "bash",
                    "-c",
                    'read -p "Press enter to connect to $1" && exec moshen "$1" "$2"',
                    "--",
                    host,
                    session_name,
                ]
            )

            if props:
                window_id = ipc.wait_for_window(pid=proc.pid)
                if window_id:
                    workspace_id = props["workspace_id"]
                    ipc.configure(window_id, workspace=workspace_id, width=props["width"])

                    _position_window(window_id, stable_id, "mosh", workspace_id)

                    column = _get_window_column(window_id, workspace_id)
                    if column is not None:
                        recorded_entries.append((workspace_id, {
                            "id": stable_id,
                            "index": column,
                            "window_id": window_id,
                            "width": props["width"],
                        }))

        entries_by_workspace: dict[int, list[dict]] = {}
        for ws_id, entry in recorded_entries:
            entries_by_workspace.setdefault(ws_id, []).append(entry)

        for ws_id, entries in entries_by_workspace.items():
            positions.upsert_entries(app="mosh", workspace_id=ws_id, entries=entries)

        positions.prune_dominated_boots(boot_id)
```

**Step 2: Update tests**

Update `tests/niri/test_restore.py` to use positions.py mocking instead of storage.lookup.

**Step 3: Run tests**

Run: `cd /home/wlritchi/.wlrenv/.worktrees/position-upsert && uv run pytest tests/niri/test_restore.py -v`

**Step 4: Commit**

```bash
git add src/wlrenv/niri/restore.py tests/niri/test_restore.py
git commit -m "refactor(restore): use positions.py for storage and ordering"
```

---

## Task 8: Update librewolf_host.py

**Files:**
- Modify: `src/wlrenv/niri/librewolf_host.py`
- Modify: `tests/niri/test_librewolf_host.py`

**Step 1: Update librewolf_host.py**

Replace storage/order_storage imports with positions. Update `handle_store()` and `handle_restore()` to use the new API.

**Step 2: Update tests**

Update test mocking to use positions.py.

**Step 3: Run tests**

Run: `cd /home/wlritchi/.wlrenv/.worktrees/position-upsert && uv run pytest tests/niri/test_librewolf_host.py -v`

**Step 4: Commit**

```bash
git add src/wlrenv/niri/librewolf_host.py tests/niri/test_librewolf_host.py
git commit -m "refactor(librewolf): use positions.py for storage"
```

---

## Task 9: Delete Deprecated Modules

**Files:**
- Delete: `src/wlrenv/niri/storage.py`
- Delete: `src/wlrenv/niri/order_storage.py`
- Delete: `tests/niri/test_storage.py`
- Delete: `tests/niri/test_order_storage.py`

**Step 1: Verify no remaining imports**

Run: `cd /home/wlritchi/.wlrenv/.worktrees/position-upsert && grep -r "from wlrenv.niri.storage import\|from wlrenv.niri import storage\|from wlrenv.niri.order_storage import\|from wlrenv.niri import order_storage" src/`
Expected: No output (no remaining imports)

**Step 2: Delete files**

```bash
cd /home/wlritchi/.wlrenv/.worktrees/position-upsert
rm src/wlrenv/niri/storage.py src/wlrenv/niri/order_storage.py
rm tests/niri/test_storage.py tests/niri/test_order_storage.py
```

**Step 3: Run all tests**

Run: `cd /home/wlritchi/.wlrenv/.worktrees/position-upsert && uv run pytest tests/niri/ -v`
Expected: All pass

**Step 4: Commit**

```bash
git add -A
git commit -m "chore: remove deprecated storage and order_storage modules"
```

---

## Task 10: Run Full Test Suite and Linting

**Step 1: Run pytest**

Run: `cd /home/wlritchi/.wlrenv/.worktrees/position-upsert && uv run pytest tests/niri/ -v`
Expected: All pass

**Step 2: Run pyright**

Run: `cd /home/wlritchi/.wlrenv/.worktrees/position-upsert && uv run pyright src/wlrenv/niri/`
Expected: No errors

**Step 3: Run ruff**

Run: `cd /home/wlritchi/.wlrenv/.worktrees/position-upsert && uv run ruff check src/wlrenv/niri/ && uv run ruff format src/wlrenv/niri/`
Expected: No errors

**Step 4: Commit any fixes**

If linting required changes, commit them.
