# Unified niri Window Tracking Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace fragmented bash scripts with a unified Python module for tracking and restoring window metadata (workspace, width) across tmux, mosh, and Librewolf.

**Architecture:** Python module at `src/wlrenv/niri/` with common storage layer, app-specific identification, and CLI entry points installed via pyproject.toml.

**Tech Stack:** Python 3.12+, stdlib only (json, subprocess, tempfile, uuid), pytest for testing

---

## Task 1: Create Module Structure and Config

**Files:**
- Create: `src/wlrenv/niri/__init__.py`
- Create: `src/wlrenv/niri/config.py`
- Test: `tests/niri/test_config.py`

**Step 1: Create test directory and initial test**

```bash
mkdir -p tests/niri
```

```python
# tests/niri/__init__.py
```

```python
# tests/niri/test_config.py
from pathlib import Path

from wlrenv.niri.config import STATE_DIR, get_storage_path


def test_state_dir_is_under_local_state() -> None:
    assert ".local/state/niri" in str(STATE_DIR)


def test_get_storage_path_returns_json_file() -> None:
    path = get_storage_path("tmux")
    assert path.suffix == ".json"
    assert path.name == "tmux.json"
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/niri/test_config.py -v`
Expected: FAIL with "ModuleNotFoundError: No module named 'wlrenv.niri'"

**Step 3: Write minimal implementation**

```python
# src/wlrenv/niri/__init__.py
"""Unified niri window tracking."""
```

```python
# src/wlrenv/niri/config.py
"""Configuration and paths for niri window tracking."""

from pathlib import Path

STATE_DIR = Path.home() / ".local" / "state" / "niri"


def get_storage_path(app: str) -> Path:
    """Return path to storage JSON for given app."""
    return STATE_DIR / f"{app}.json"
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/niri/test_config.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/wlrenv/niri/ tests/niri/
git commit -m "feat(niri): add module structure and config"
```

---

## Task 2: Implement Storage Layer

**Files:**
- Create: `src/wlrenv/niri/storage.py`
- Test: `tests/niri/test_storage.py`

**Step 1: Write failing tests**

```python
# tests/niri/test_storage.py
import json
from pathlib import Path

import pytest

from wlrenv.niri.storage import _load, _save, lookup, store_entry


@pytest.fixture
def temp_state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Use temporary directory for state."""
    import wlrenv.niri.config as config
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    return tmp_path


def test_load_returns_empty_structure_for_missing_file(temp_state_dir: Path) -> None:
    data = _load("tmux")
    assert data == {"version": 1, "entries": {}}


def test_save_creates_file(temp_state_dir: Path) -> None:
    data = {"version": 1, "entries": {"test": {"workspace": 1, "width": 50}}}
    _save("tmux", data)

    path = temp_state_dir / "tmux.json"
    assert path.exists()
    assert json.loads(path.read_text()) == data


def test_save_is_atomic(temp_state_dir: Path) -> None:
    """No .tmp files left behind after save."""
    _save("tmux", {"version": 1, "entries": {}})

    tmp_files = list(temp_state_dir.glob("*.tmp"))
    assert tmp_files == []


def test_store_entry_persists_data(temp_state_dir: Path) -> None:
    store_entry("tmux", "mysession", workspace=2, width=50)

    data = _load("tmux")
    assert data["entries"]["mysession"] == {"workspace": 2, "width": 50}


def test_lookup_returns_stored_data(temp_state_dir: Path) -> None:
    store_entry("tmux", "mysession", workspace=3, width=70)

    result = lookup("tmux", "mysession")
    assert result == {"workspace": 3, "width": 70}


def test_lookup_returns_none_for_missing(temp_state_dir: Path) -> None:
    result = lookup("tmux", "nonexistent")
    assert result is None
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/niri/test_storage.py -v`
Expected: FAIL with "cannot import name '_load' from 'wlrenv.niri.storage'"

**Step 3: Write implementation**

```python
# src/wlrenv/niri/storage.py
"""Persistent storage for window metadata."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from wlrenv.niri.config import STATE_DIR, get_storage_path


def _load(app: str) -> dict[str, Any]:
    """Load storage data for app, returning empty structure if missing."""
    path = get_storage_path(app)
    if path.exists():
        return json.loads(path.read_text())  # type: ignore[no-any-return]
    return {"version": 1, "entries": {}}


def _save(app: str, data: dict[str, Any]) -> None:
    """Atomically save storage data for app."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = get_storage_path(app)

    # Write to temp file, then atomic rename
    fd, tmp_path = tempfile.mkstemp(dir=STATE_DIR, suffix=".json.tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.rename(tmp_path, path)
    except BaseException:
        os.unlink(tmp_path)
        raise


def store_entry(app: str, identity: str, workspace: int, width: int) -> None:
    """Store workspace/width for given identity."""
    data = _load(app)
    data["entries"][identity] = {"workspace": workspace, "width": width}
    _save(app, data)


def lookup(app: str, identity: str) -> dict[str, int] | None:
    """Look up stored workspace/width for identity, or None if not found."""
    data = _load(app)
    return data["entries"].get(identity)  # type: ignore[no-any-return]
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/niri/test_storage.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/wlrenv/niri/storage.py tests/niri/test_storage.py
git commit -m "feat(niri): add storage layer with atomic writes"
```

---

## Task 3: Implement niri IPC Wrapper

**Files:**
- Create: `src/wlrenv/niri/ipc.py`
- Test: `tests/niri/test_ipc.py`

**Step 1: Write failing tests**

```python
# tests/niri/test_ipc.py
from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from wlrenv.niri.ipc import (
    NiriError,
    Window,
    Output,
    get_windows,
    get_outputs,
    configure,
)


def make_window_json(
    id: int = 1,
    title: str = "Test",
    app_id: str = "Alacritty",
    pid: int = 1234,
    workspace_id: int = 1,
    tile_width: float = 1535.0,
) -> dict[str, Any]:
    return {
        "id": id,
        "title": title,
        "app_id": app_id,
        "pid": pid,
        "workspace_id": workspace_id,
        "is_focused": False,
        "is_floating": False,
        "is_urgent": False,
        "layout": {
            "pos_in_scrolling_layout": [1, 1],
            "tile_size": [tile_width, 1000.0],
            "window_size": [int(tile_width) - 2, 998],
            "tile_pos_in_workspace_view": None,
            "window_offset_in_tile": [1.0, 1.0],
        },
    }


def make_output_json(
    name: str = "eDP-1",
    width: int = 3072,
    height: int = 1920,
) -> dict[str, Any]:
    return {
        "name": name,
        "make": "Test",
        "model": "Test",
        "serial": None,
        "physical_size": [340, 220],
        "modes": [{"width": width, "height": height, "refresh_rate": 60000, "is_preferred": True}],
        "current_mode": 0,
        "vrr_supported": False,
        "vrr_enabled": False,
        "logical": {"x": 0, "y": 0, "width": width, "height": height, "scale": 1.0, "transform": "Normal"},
    }


@patch("wlrenv.niri.ipc._run_niri_msg")
def test_get_windows_parses_response(mock_run: MagicMock) -> None:
    mock_run.return_value = [make_window_json(id=1, title="Test Window")]

    windows = get_windows()

    assert len(windows) == 1
    assert windows[0].id == 1
    assert windows[0].title == "Test Window"


@patch("wlrenv.niri.ipc._run_niri_msg")
def test_get_windows_filters_by_app_id(mock_run: MagicMock) -> None:
    mock_run.return_value = [
        make_window_json(id=1, app_id="Alacritty"),
        make_window_json(id=2, app_id="firefox"),
    ]

    windows = get_windows(app_id="Alacritty")

    assert len(windows) == 1
    assert windows[0].id == 1


@patch("wlrenv.niri.ipc._run_niri_msg")
def test_get_outputs_parses_response(mock_run: MagicMock) -> None:
    mock_run.return_value = [make_output_json(name="eDP-1", width=3072)]

    outputs = get_outputs()

    assert len(outputs) == 1
    assert outputs[0].name == "eDP-1"
    assert outputs[0].width == 3072


@patch("wlrenv.niri.ipc._run_niri_msg")
def test_configure_calls_correct_actions(mock_run: MagicMock) -> None:
    mock_run.return_value = None

    configure(window_id=42, workspace=2, width=50)

    calls = mock_run.call_args_list
    assert len(calls) == 2
    # Check workspace action
    assert "move-window-to-workspace" in str(calls[0])
    # Check width action
    assert "set-window-width" in str(calls[1])


@patch("wlrenv.niri.ipc._run_niri_msg")
def test_configure_skips_none_values(mock_run: MagicMock) -> None:
    mock_run.return_value = None

    configure(window_id=42, workspace=2, width=None)

    calls = mock_run.call_args_list
    assert len(calls) == 1
    assert "move-window-to-workspace" in str(calls[0])
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/niri/test_ipc.py -v`
Expected: FAIL with "cannot import name 'NiriError' from 'wlrenv.niri.ipc'"

**Step 3: Write implementation**

```python
# src/wlrenv/niri/ipc.py
"""niri IPC wrapper for window management."""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass
from typing import Any


class NiriError(Exception):
    """Error communicating with niri."""


@dataclass
class Window:
    """A niri window."""

    id: int
    title: str
    app_id: str
    pid: int
    workspace_id: int
    tile_width: float
    tile_height: float


@dataclass
class Output:
    """A niri output (monitor)."""

    name: str
    width: int
    height: int


@dataclass
class Workspace:
    """A niri workspace."""

    id: int
    output: str


def _run_niri_msg(args: list[str], *, json_output: bool = True) -> Any:
    """Run niri msg command and return parsed output."""
    if not os.environ.get("NIRI_SOCKET"):
        raise NiriError("NIRI_SOCKET not set")

    cmd = ["niri", "msg"]
    if json_output:
        cmd.append("--json")
    cmd.extend(args)

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as e:
        raise NiriError(f"niri msg failed: {e.stderr}") from e

    if json_output and result.stdout.strip():
        return json.loads(result.stdout)
    return None


def get_windows(app_id: str | None = None) -> list[Window]:
    """Get all windows, optionally filtered by app_id."""
    data = _run_niri_msg(["windows"])

    windows = []
    for w in data:
        if app_id and w.get("app_id") != app_id:
            continue
        windows.append(
            Window(
                id=w["id"],
                title=w.get("title", ""),
                app_id=w.get("app_id", ""),
                pid=w["pid"],
                workspace_id=w["workspace_id"],
                tile_width=w["layout"]["tile_size"][0],
                tile_height=w["layout"]["tile_size"][1],
            )
        )
    return windows


def get_outputs() -> list[Output]:
    """Get all outputs with logical dimensions."""
    data = _run_niri_msg(["outputs"])

    outputs = []
    for o in data:
        outputs.append(
            Output(
                name=o["name"],
                width=o["logical"]["width"],
                height=o["logical"]["height"],
            )
        )
    return outputs


def get_workspaces() -> list[Workspace]:
    """Get all workspaces with output mapping."""
    data = _run_niri_msg(["workspaces"])

    return [Workspace(id=w["id"], output=w["output"]) for w in data]


def find_window_by_title(title: str) -> Window | None:
    """Find a window by exact title match."""
    windows = get_windows()
    for w in windows:
        if w.title == title:
            return w
    return None


def find_window_by_pid(pid: int) -> Window | None:
    """Find a window by PID."""
    windows = get_windows()
    for w in windows:
        if w.pid == pid:
            return w
    return None


def wait_for_window(pid: int, timeout: float = 5.0) -> int | None:
    """Wait for a window with given PID to appear, return window_id or None."""
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        window = find_window_by_pid(pid)
        if window:
            return window.id
        time.sleep(0.1)
    return None


def configure(window_id: int, workspace: int | None, width: int | None) -> None:
    """Configure window workspace and/or width."""
    if workspace is not None:
        _run_niri_msg(
            [
                "action",
                "move-window-to-workspace",
                "--window-id",
                str(window_id),
                "--focus",
                "false",
                str(workspace),
            ],
            json_output=False,
        )

    if width is not None:
        _run_niri_msg(
            ["action", "set-window-width", "--id", str(window_id), f"{width}%"],
            json_output=False,
        )
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/niri/test_ipc.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/wlrenv/niri/ipc.py tests/niri/test_ipc.py
git commit -m "feat(niri): add IPC wrapper for niri msg"
```

---

## Task 4: Implement Terminal Identification

**Files:**
- Create: `src/wlrenv/niri/identify.py`
- Test: `tests/niri/test_identify.py`

**Step 1: Write failing tests**

```python
# tests/niri/test_identify.py
from __future__ import annotations

from dataclasses import dataclass

import pytest

from wlrenv.niri.identify import identify_tmux, identify_mosh, ProcessInfo


@dataclass
class MockProc:
    comm: str
    args: list[str]


def test_identify_tmux_with_client() -> None:
    proc = ProcessInfo(comm="tmux: client", args=["tmux", "attach-session", "-t", "main"])
    result = identify_tmux(proc)
    assert result == "main"


def test_identify_tmux_with_bare_tmux() -> None:
    proc = ProcessInfo(comm="tmux", args=["tmux", "attach-session", "-t", "work"])
    result = identify_tmux(proc)
    assert result == "work"


def test_identify_tmux_without_session() -> None:
    proc = ProcessInfo(comm="tmux", args=["tmux", "new-session"])
    result = identify_tmux(proc)
    assert result is None


def test_identify_tmux_wrong_process() -> None:
    proc = ProcessInfo(comm="bash", args=["bash"])
    result = identify_tmux(proc)
    assert result is None


def test_identify_mosh_with_session() -> None:
    proc = ProcessInfo(comm="moshen", args=["moshen", "server.example.com", "main"])
    result = identify_mosh(proc)
    assert result == "server.example.com:main"


def test_identify_mosh_default_session() -> None:
    proc = ProcessInfo(comm="moshen", args=["moshen", "server.example.com"])
    result = identify_mosh(proc)
    assert result == "server.example.com:main"


def test_identify_mosh_wrong_process() -> None:
    proc = ProcessInfo(comm="mosh-client", args=["mosh-client", "..."])
    result = identify_mosh(proc)
    assert result is None
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/niri/test_identify.py -v`
Expected: FAIL with "cannot import name 'identify_tmux' from 'wlrenv.niri.identify'"

**Step 3: Write implementation**

```python
# src/wlrenv/niri/identify.py
"""Identify terminal sessions from child processes."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class ProcessInfo:
    """Information about a process."""

    comm: str
    args: list[str]


def identify_tmux(proc: ProcessInfo) -> str | None:
    """Extract tmux session name from process info."""
    if not proc.comm.startswith("tmux"):
        return None

    # Look for -t <session> in args
    args_str = " ".join(proc.args)
    match = re.search(r"-t\s+([^\s]+)", args_str)
    if match:
        return match.group(1)

    return None


def identify_mosh(proc: ProcessInfo) -> str | None:
    """Extract host:session from moshen process info."""
    if proc.comm != "moshen":
        return None

    # moshen <host> [session]
    if len(proc.args) >= 2:
        host = proc.args[1]
        session = proc.args[2] if len(proc.args) >= 3 else "main"
        return f"{host}:{session}"

    return None
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/niri/test_identify.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/wlrenv/niri/identify.py tests/niri/test_identify.py
git commit -m "feat(niri): add tmux/mosh session identification"
```

---

## Task 5: Implement Librewolf URL Matching

**Files:**
- Create: `src/wlrenv/niri/librewolf.py`
- Test: `tests/niri/test_librewolf.py`

**Step 1: Write failing tests**

```python
# tests/niri/test_librewolf.py
from __future__ import annotations

from pathlib import Path

import pytest

from wlrenv.niri.librewolf import UrlMatcher


@pytest.fixture
def temp_state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Use temporary directory for state."""
    import wlrenv.niri.config as config
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    return tmp_path


def test_matcher_creates_new_uuid_for_unknown_urls(temp_state_dir: Path) -> None:
    matcher = UrlMatcher.load()

    uuid = matcher.match_or_create(["https://example.com"])

    assert uuid is not None
    assert len(uuid) == 36  # UUID format


def test_matcher_returns_same_uuid_for_same_urls(temp_state_dir: Path) -> None:
    matcher = UrlMatcher.load()

    uuid1 = matcher.match_or_create(["https://example.com", "https://test.com"])
    matcher.save()

    # Reload and match again
    matcher2 = UrlMatcher.load()
    uuid2 = matcher2.match_or_create(["https://example.com", "https://test.com"])

    assert uuid1 == uuid2


def test_matcher_matches_by_overlap(temp_state_dir: Path) -> None:
    matcher = UrlMatcher.load()

    # Create entry with some URLs
    uuid1 = matcher.match_or_create(["https://a.com", "https://b.com", "https://c.com"])
    matcher.save()

    # Match with partial overlap (2 of 3)
    matcher2 = UrlMatcher.load()
    uuid2 = matcher2.match_or_create(["https://a.com", "https://b.com", "https://d.com"])

    assert uuid1 == uuid2


def test_matcher_removes_matched_from_pool(temp_state_dir: Path) -> None:
    matcher = UrlMatcher.load()

    # Create two entries
    uuid1 = matcher.match_or_create(["https://a.com"])
    uuid2 = matcher.match_or_create(["https://b.com"])
    matcher.save()

    # In a new session, match first one - should remove from pool
    matcher2 = UrlMatcher.load()
    result1 = matcher2.match_or_create(["https://a.com"])
    result2 = matcher2.match_or_create(["https://a.com"])  # Same URLs, but first is taken

    assert result1 == uuid1
    assert result2 == uuid2  # Falls back to second entry or creates new


def test_matcher_updates_urls_on_match(temp_state_dir: Path) -> None:
    matcher = UrlMatcher.load()

    uuid = matcher.match_or_create(["https://old.com"])
    matcher.save()

    # Match with different URLs
    matcher2 = UrlMatcher.load()
    matcher2.match_or_create(["https://new.com"])
    matcher2.save()

    # Verify URLs were updated
    matcher3 = UrlMatcher.load()
    # The entry should now have the new URL
    assert matcher3.entries[0]["urls"] == ["https://new.com"]
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/niri/test_librewolf.py -v`
Expected: FAIL with "cannot import name 'UrlMatcher' from 'wlrenv.niri.librewolf'"

**Step 3: Write implementation**

```python
# src/wlrenv/niri/librewolf.py
"""Librewolf window identification via URL matching."""

from __future__ import annotations

import json
import os
import tempfile
import uuid as uuid_lib
from typing import Any

from wlrenv.niri.config import STATE_DIR


def _get_identities_path() -> Any:
    return STATE_DIR / "librewolf-identities.json"


class UrlMatcher:
    """Stateful URL matcher for Librewolf window identification."""

    def __init__(self, entries: list[dict[str, Any]]) -> None:
        self.entries = entries
        self.available: set[int] = set(range(len(entries)))

    @classmethod
    def load(cls) -> "UrlMatcher":
        """Load matcher state from disk."""
        path = _get_identities_path()
        if path.exists():
            data = json.loads(path.read_text())
            return cls(data.get("entries", []))
        return cls([])

    def match_or_create(self, urls: list[str]) -> str:
        """Find best match from available pool, or create new UUID."""
        url_set = set(urls)

        best_idx: int | None = None
        best_overlap = 0

        for i in self.available:
            entry_urls = set(self.entries[i]["urls"])
            overlap = len(url_set & entry_urls)
            if overlap > best_overlap:
                best_idx = i
                best_overlap = overlap

        if best_idx is not None:
            self.available.remove(best_idx)
            # Update stored URLs to current
            self.entries[best_idx]["urls"] = urls
            return self.entries[best_idx]["uuid"]  # type: ignore[no-any-return]

        # No match - create new entry
        new_uuid = str(uuid_lib.uuid4())
        self.entries.append({"uuid": new_uuid, "urls": urls})
        return new_uuid

    def save(self) -> None:
        """Persist matcher state to disk."""
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        path = _get_identities_path()

        data = {"version": 1, "entries": self.entries}

        # Atomic write
        fd, tmp_path = tempfile.mkstemp(dir=STATE_DIR, suffix=".json.tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2)
            os.rename(tmp_path, path)
        except BaseException:
            os.unlink(tmp_path)
            raise
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/niri/test_librewolf.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/wlrenv/niri/librewolf.py tests/niri/test_librewolf.py
git commit -m "feat(niri): add Librewolf URL matching for window identification"
```

---

## Task 6: Implement Terminal Tracker

**Files:**
- Create: `src/wlrenv/niri/track.py`
- Test: `tests/niri/test_track.py`

**Step 1: Write failing tests**

```python
# tests/niri/test_track.py
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from wlrenv.niri.ipc import Window, Output, Workspace
from wlrenv.niri.track import track_terminals, get_child_processes, calculate_width_percent


@pytest.fixture
def temp_state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Use temporary directory for state."""
    import wlrenv.niri.config as config
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    return tmp_path


def test_calculate_width_percent_rounds_to_10() -> None:
    # 1535 / 3072 = 49.97% -> rounds to 50
    assert calculate_width_percent(1535.0, 3072) == 50

    # 3070 / 3072 = 99.9% -> rounds to 100
    assert calculate_width_percent(3070.0, 3072) == 100

    # 768 / 3072 = 25% -> exactly 30 (rounds up from 25)
    assert calculate_width_percent(768.0, 3072) == 30


def make_window(
    id: int = 1,
    pid: int = 1234,
    workspace_id: int = 1,
    tile_width: float = 1535.0,
) -> Window:
    return Window(
        id=id,
        title="Alacritty",
        app_id="Alacritty",
        pid=pid,
        workspace_id=workspace_id,
        tile_width=tile_width,
        tile_height=1000.0,
    )


@patch("wlrenv.niri.track.get_child_processes")
@patch("wlrenv.niri.ipc.get_workspaces")
@patch("wlrenv.niri.ipc.get_outputs")
@patch("wlrenv.niri.ipc.get_windows")
def test_track_terminals_stores_tmux_session(
    mock_windows: MagicMock,
    mock_outputs: MagicMock,
    mock_workspaces: MagicMock,
    mock_children: MagicMock,
    temp_state_dir: Path,
) -> None:
    from wlrenv.niri.identify import ProcessInfo
    from wlrenv.niri.storage import lookup

    mock_windows.return_value = [make_window(id=1, pid=1000, workspace_id=2)]
    mock_outputs.return_value = [Output(name="eDP-1", width=3072, height=1920)]
    mock_workspaces.return_value = [Workspace(id=2, output="eDP-1")]
    mock_children.return_value = [
        ProcessInfo(comm="tmux: client", args=["tmux", "attach-session", "-t", "mywork"])
    ]

    track_terminals()

    result = lookup("tmux", "mywork")
    assert result is not None
    assert result["workspace"] == 2
    assert result["width"] == 50
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/niri/test_track.py -v`
Expected: FAIL with "cannot import name 'track_terminals' from 'wlrenv.niri.track'"

**Step 3: Write implementation**

```python
# src/wlrenv/niri/track.py
"""Track terminal windows and store their metadata."""

from __future__ import annotations

import subprocess

from wlrenv.niri import ipc
from wlrenv.niri.identify import ProcessInfo, identify_mosh, identify_tmux
from wlrenv.niri.storage import store_entry


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
            # Get comm (process name)
            comm_result = subprocess.run(
                ["ps", "-o", "comm=", "-p", str(cpid)],
                capture_output=True,
                text=True,
            )
            comm = comm_result.stdout.strip()

            # Get full args
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

    for window in windows:
        # Get output for this window's workspace
        ws = workspaces.get(window.workspace_id)
        if not ws:
            continue
        output = outputs.get(ws.output)
        if not output:
            continue

        width_percent = calculate_width_percent(window.tile_width, output.width)

        # Check child processes for known apps
        children = get_child_processes(window.pid)
        for child in children:
            if identity := identify_tmux(child):
                store_entry("tmux", identity, window.workspace_id, width_percent)
                break
            if identity := identify_mosh(child):
                store_entry("mosh", identity, window.workspace_id, width_percent)
                break
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/niri/test_track.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/wlrenv/niri/track.py tests/niri/test_track.py
git commit -m "feat(niri): add unified terminal tracker"
```

---

## Task 7: Implement Restore Logic

**Files:**
- Create: `src/wlrenv/niri/restore.py`
- Test: `tests/niri/test_restore.py`

**Step 1: Write failing tests**

```python
# tests/niri/test_restore.py
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from wlrenv.niri.restore import restore_tmux, restore_mosh


@pytest.fixture
def temp_state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Use temporary directory for state."""
    import wlrenv.niri.config as config
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    return tmp_path


@patch("wlrenv.niri.restore.spawn_terminal")
@patch("wlrenv.niri.restore.get_detached_tmux_sessions")
@patch("wlrenv.niri.ipc.wait_for_window")
@patch("wlrenv.niri.ipc.configure")
def test_restore_tmux_spawns_and_configures(
    mock_configure: MagicMock,
    mock_wait: MagicMock,
    mock_sessions: MagicMock,
    mock_spawn: MagicMock,
    temp_state_dir: Path,
) -> None:
    from wlrenv.niri.storage import store_entry

    # Set up stored data
    store_entry("tmux", "work", workspace=2, width=70)

    # Mock session list
    mock_sessions.return_value = ["work"]

    # Mock spawn returning a process with PID
    mock_proc = MagicMock()
    mock_proc.pid = 12345
    mock_spawn.return_value = mock_proc

    # Mock window appearing
    mock_wait.return_value = 42

    restore_tmux()

    mock_spawn.assert_called_once()
    mock_wait.assert_called_once_with(pid=12345)
    mock_configure.assert_called_once_with(42, workspace=2, width=70)


@patch("wlrenv.niri.restore.spawn_terminal")
@patch("wlrenv.niri.restore.get_detached_tmux_sessions")
@patch("wlrenv.niri.ipc.wait_for_window")
@patch("wlrenv.niri.ipc.configure")
def test_restore_tmux_skips_wait_if_no_props(
    mock_configure: MagicMock,
    mock_wait: MagicMock,
    mock_sessions: MagicMock,
    mock_spawn: MagicMock,
    temp_state_dir: Path,
) -> None:
    # No stored data for this session
    mock_sessions.return_value = ["unknown"]
    mock_proc = MagicMock()
    mock_proc.pid = 12345
    mock_spawn.return_value = mock_proc

    restore_tmux()

    mock_spawn.assert_called_once()
    mock_wait.assert_not_called()
    mock_configure.assert_not_called()
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/niri/test_restore.py -v`
Expected: FAIL with "cannot import name 'restore_tmux' from 'wlrenv.niri.restore'"

**Step 3: Write implementation**

```python
# src/wlrenv/niri/restore.py
"""Restore terminal windows to their saved workspaces."""

from __future__ import annotations

import os
import subprocess

from wlrenv.niri import ipc
from wlrenv.niri.storage import lookup


def get_detached_tmux_sessions() -> list[str]:
    """Get list of detached tmux session names."""
    try:
        result = subprocess.run(
            ["tmux", "list-sessions", "-F", "#{session_name}", "-f", "#{?#{session_attached},0,1}"],
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

    state_dir = os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state"))
    sessions_file = Path(state_dir) / "moshen" / "sessions"

    if not sessions_file.exists():
        return []

    sessions = []
    for line in sessions_file.read_text().strip().split("\n"):
        if not line:
            continue
        parts = line.split(":")
        if len(parts) >= 2:
            host = base64.b64decode(parts[0]).decode()
            session = base64.b64decode(parts[1]).decode()
            sessions.append((host, session))

    return sessions


def spawn_terminal(args: list[str]) -> subprocess.Popen[bytes]:
    """Spawn a terminal with the given command."""
    terminal = os.environ.get("TERMINAL", "alacritty")
    return subprocess.Popen([terminal, "-e", *args])


def restore_tmux() -> None:
    """Restore detached tmux sessions to their saved workspaces."""
    sessions = get_detached_tmux_sessions()

    for session in sessions:
        # Skip temporary sessions (git commit hashes)
        if len(session) == 7 and all(c in "0123456789abcdef" for c in session):
            continue

        props = lookup("tmux", session)
        proc = spawn_terminal(["tmux", "attach-session", "-t", session])

        if props:
            window_id = ipc.wait_for_window(pid=proc.pid)
            if window_id:
                ipc.configure(window_id, workspace=props["workspace"], width=props["width"])


def restore_mosh() -> None:
    """Restore mosh sessions to their saved workspaces."""
    sessions = read_moshen_sessions()

    for host, session_name in sessions:
        identity = f"{host}:{session_name}"
        props = lookup("mosh", identity)
        proc = spawn_terminal(["moshen", host, session_name])

        if props:
            window_id = ipc.wait_for_window(pid=proc.pid)
            if window_id:
                ipc.configure(window_id, workspace=props["workspace"], width=props["width"])
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/niri/test_restore.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/wlrenv/niri/restore.py tests/niri/test_restore.py
git commit -m "feat(niri): add restore logic for tmux and mosh"
```

---

## Task 8: Implement CLI Entry Points

**Files:**
- Create: `src/wlrenv/niri/cli.py`
- Modify: `pyproject.toml` (add entry points)
- Test: `tests/niri/test_cli.py`

**Step 1: Write failing tests**

```python
# tests/niri/test_cli.py
from __future__ import annotations

from unittest.mock import patch, MagicMock

from wlrenv.niri.cli import track_terminals_cli, restore_tmux_cli, restore_mosh_cli


@patch("wlrenv.niri.cli.track_terminals")
def test_track_terminals_cli_calls_track(mock_track: MagicMock) -> None:
    track_terminals_cli()
    mock_track.assert_called_once()


@patch("wlrenv.niri.cli.restore_tmux")
def test_restore_tmux_cli_calls_restore(mock_restore: MagicMock) -> None:
    restore_tmux_cli()
    mock_restore.assert_called_once()


@patch("wlrenv.niri.cli.restore_mosh")
def test_restore_mosh_cli_calls_restore(mock_restore: MagicMock) -> None:
    restore_mosh_cli()
    mock_restore.assert_called_once()
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/niri/test_cli.py -v`
Expected: FAIL with "cannot import name 'track_terminals_cli' from 'wlrenv.niri.cli'"

**Step 3: Write implementation**

```python
# src/wlrenv/niri/cli.py
"""CLI entry points for niri window tracking."""

from __future__ import annotations

import sys

from wlrenv.niri.ipc import NiriError
from wlrenv.niri.restore import restore_mosh, restore_tmux
from wlrenv.niri.track import track_terminals


def track_terminals_cli() -> None:
    """CLI entry point for terminal tracking."""
    try:
        track_terminals()
    except NiriError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def restore_tmux_cli() -> None:
    """CLI entry point for tmux restoration."""
    try:
        restore_tmux()
    except NiriError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def restore_mosh_cli() -> None:
    """CLI entry point for mosh restoration."""
    try:
        restore_mosh()
    except NiriError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/niri/test_cli.py -v`
Expected: PASS

**Step 5: Update pyproject.toml with entry points**

Add to `pyproject.toml` under `[project.scripts]`:

```toml
[project.scripts]
xonsh = 'xonsh.main:main'
wlr-niri-track-terminals = 'wlrenv.niri.cli:track_terminals_cli'
wlr-niri-restore-tmux = 'wlrenv.niri.cli:restore_tmux_cli'
wlr-niri-restore-mosh = 'wlrenv.niri.cli:restore_mosh_cli'
```

**Step 6: Commit**

```bash
git add src/wlrenv/niri/cli.py tests/niri/test_cli.py pyproject.toml
git commit -m "feat(niri): add CLI entry points"
```

---

## Task 9: Implement Librewolf Native Host

**Files:**
- Create: `src/wlrenv/niri/librewolf_host.py`
- Modify: `src/wlrenv/niri/cli.py` (add entry point)
- Modify: `pyproject.toml` (add entry point)
- Test: `tests/niri/test_librewolf_host.py`

**Step 1: Write failing tests**

```python
# tests/niri/test_librewolf_host.py
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from wlrenv.niri.librewolf_host import handle_message


@pytest.fixture
def temp_state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Use temporary directory for state."""
    import wlrenv.niri.config as config
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    return tmp_path


@patch("wlrenv.niri.librewolf_host.ipc")
def test_handle_store_message(mock_ipc: MagicMock, temp_state_dir: Path) -> None:
    from wlrenv.niri.ipc import Window
    from wlrenv.niri.storage import lookup

    mock_ipc.find_window_by_title.return_value = Window(
        id=42,
        title="GitHub",
        app_id="librewolf",
        pid=1234,
        workspace_id=2,
        tile_width=1535.0,
        tile_height=1000.0,
    )
    mock_ipc.get_outputs.return_value = [MagicMock(name="eDP-1", width=3072)]
    mock_ipc.get_workspaces.return_value = [MagicMock(id=2, output="eDP-1")]

    message = {
        "action": "store_mappings_batch",
        "windows": [
            {
                "window_title": "GitHub",
                "tabs": [{"url": "https://github.com", "title": "GitHub"}],
            }
        ],
    }

    response = handle_message(message)

    assert response["success"] is True


@patch("wlrenv.niri.librewolf_host.ipc")
def test_handle_restore_message(mock_ipc: MagicMock, temp_state_dir: Path) -> None:
    from wlrenv.niri.ipc import Window
    from wlrenv.niri.storage import store_entry
    from wlrenv.niri.librewolf import UrlMatcher

    # Set up stored data
    matcher = UrlMatcher.load()
    uuid = matcher.match_or_create(["https://github.com"])
    matcher.save()
    store_entry("librewolf", uuid, workspace=3, width=70)

    mock_ipc.find_window_by_title.return_value = Window(
        id=42,
        title="GitHub",
        app_id="librewolf",
        pid=1234,
        workspace_id=1,
        tile_width=1535.0,
        tile_height=1000.0,
    )

    message = {
        "action": "restore_workspaces",
        "windows": [
            {
                "window_title": "GitHub",
                "tabs": [{"url": "https://github.com", "title": "GitHub"}],
            }
        ],
    }

    response = handle_message(message)

    assert response["success"] is True
    mock_ipc.configure.assert_called_once()


def test_handle_ping_message(temp_state_dir: Path) -> None:
    message = {"action": "ping"}
    response = handle_message(message)
    assert response["success"] is True
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/niri/test_librewolf_host.py -v`
Expected: FAIL with "cannot import name 'handle_message' from 'wlrenv.niri.librewolf_host'"

**Step 3: Write implementation**

```python
# src/wlrenv/niri/librewolf_host.py
"""Native messaging host for Librewolf workspace tracking."""

from __future__ import annotations

import json
import struct
import sys
from typing import Any

from wlrenv.niri import ipc
from wlrenv.niri.librewolf import UrlMatcher
from wlrenv.niri.storage import lookup, store_entry
from wlrenv.niri.track import calculate_width_percent


def handle_message(message: dict[str, Any]) -> dict[str, Any]:
    """Handle a message from the browser extension."""
    action = message.get("action")
    request_id = message.get("request_id")

    try:
        if action == "ping":
            return {"success": True, "request_id": request_id}

        if action == "store_mappings_batch":
            return handle_store(message, request_id)

        if action == "restore_workspaces":
            return handle_restore(message, request_id)

        return {"success": False, "error": f"Unknown action: {action}", "request_id": request_id}

    except Exception as e:
        return {"success": False, "error": str(e), "request_id": request_id}


def handle_store(message: dict[str, Any], request_id: str | None) -> dict[str, Any]:
    """Handle store_mappings_batch action."""
    windows = message.get("windows", [])

    # Sort by URL count descending for greedy matching
    windows = sorted(windows, key=lambda w: len(w.get("tabs", [])), reverse=True)

    # Get niri state once
    outputs = {o.name: o for o in ipc.get_outputs()}
    workspaces = {w.id: w for w in ipc.get_workspaces()}

    matcher = UrlMatcher.load()
    stored_count = 0

    for win in windows:
        urls = [t["url"] for t in win.get("tabs", [])]
        title = win.get("window_title", "")

        uuid = matcher.match_or_create(urls)
        niri_window = ipc.find_window_by_title(title)

        if niri_window:
            ws = workspaces.get(niri_window.workspace_id)
            if ws:
                output = outputs.get(ws.output)
                if output:
                    width = calculate_width_percent(niri_window.tile_width, output.width)
                    store_entry("librewolf", uuid, niri_window.workspace_id, width)
                    stored_count += 1

    matcher.save()
    return {"success": True, "stored_count": stored_count, "request_id": request_id}


def handle_restore(message: dict[str, Any], request_id: str | None) -> dict[str, Any]:
    """Handle restore_workspaces action."""
    windows = message.get("windows", [])

    # Sort by URL count descending for greedy matching
    windows = sorted(windows, key=lambda w: len(w.get("tabs", [])), reverse=True)

    matcher = UrlMatcher.load()
    moved_count = 0

    for win in windows:
        urls = [t["url"] for t in win.get("tabs", [])]
        title = win.get("window_title", "")

        uuid = matcher.match_or_create(urls)
        props = lookup("librewolf", uuid)
        niri_window = ipc.find_window_by_title(title)

        if niri_window and props:
            ipc.configure(niri_window.id, workspace=props["workspace"], width=props["width"])
            moved_count += 1

    matcher.save()
    return {"success": True, "moved_count": moved_count, "request_id": request_id}


def read_message() -> dict[str, Any] | None:
    """Read a native messaging message from stdin."""
    raw_length = sys.stdin.buffer.read(4)
    if not raw_length:
        return None
    length = struct.unpack("@I", raw_length)[0]
    data = sys.stdin.buffer.read(length).decode("utf-8")
    return json.loads(data)  # type: ignore[no-any-return]


def write_message(message: dict[str, Any]) -> None:
    """Write a native messaging message to stdout."""
    encoded = json.dumps(message).encode("utf-8")
    sys.stdout.buffer.write(struct.pack("@I", len(encoded)))
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()


def main() -> None:
    """Main loop for native messaging host."""
    while True:
        message = read_message()
        if message is None:
            break
        response = handle_message(message)
        write_message(response)
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/niri/test_librewolf_host.py -v`
Expected: PASS

**Step 5: Add CLI entry point**

Add to `src/wlrenv/niri/cli.py`:

```python
from wlrenv.niri.librewolf_host import main as librewolf_host_main


def librewolf_native_host_cli() -> None:
    """CLI entry point for Librewolf native messaging host."""
    librewolf_host_main()
```

Add to `pyproject.toml` under `[project.scripts]`:

```toml
wlr-niri-librewolf-host = 'wlrenv.niri.cli:librewolf_native_host_cli'
```

**Step 6: Commit**

```bash
git add src/wlrenv/niri/librewolf_host.py tests/niri/test_librewolf_host.py src/wlrenv/niri/cli.py pyproject.toml
git commit -m "feat(niri): add Librewolf native messaging host"
```

---

## Task 10: Update Systemd Services

**Files:**
- Modify: `machines/systemd-services.nix` (update service definitions)

**Step 1: Update service to use new entry point**

Find the existing `wlr-tmux-niri-tracker` service and update to call `wlr-niri-track-terminals`.

The service should:
- Run `wlr-niri-track-terminals` instead of the old bash script
- Keep the same timer interval (30 seconds)
- Keep `ConditionEnvironment=NIRI_SOCKET`

**Step 2: Commit**

```bash
git add machines/systemd-services.nix
git commit -m "chore(niri): update systemd services for new entry points"
```

---

## Task 11: Remove Old Bash Scripts

**Files:**
- Delete: `bin/tmux/wlr-tmux-niri-tracker`
- Delete: `bin/tmux/wlr-open-tmux-sessions`
- Delete: `bin/tmux/wlr-tmux-resurrect-save-workspaces`
- Delete: `bin/tmux/wlr-tmux-resurrect-restore-workspaces`
- Delete: `bin/wayland/wlr-niri-configure-window`
- Delete: `bin/ssh/wlr-restore-moshen-sessions`

**Step 1: Remove files**

```bash
rm bin/tmux/wlr-tmux-niri-tracker
rm bin/tmux/wlr-open-tmux-sessions
rm bin/tmux/wlr-tmux-resurrect-save-workspaces
rm bin/tmux/wlr-tmux-resurrect-restore-workspaces
rm bin/wayland/wlr-niri-configure-window
rm bin/ssh/wlr-restore-moshen-sessions
```

**Step 2: Update tmux.conf to remove resurrect hooks**

Remove these lines from tmux configuration:
```bash
set -g @resurrect-hook-post-save-layout 'wlr-tmux-resurrect-save-workspaces'
set -g @resurrect-hook-pre-restore-pane-processes 'wlr-tmux-resurrect-restore-workspaces'
```

**Step 3: Commit**

```bash
git add -u
git commit -m "chore(niri): remove deprecated bash scripts"
```

---

## Task 12: Run Full Test Suite and Manual Verification

**Step 1: Run all tests**

```bash
uv run pytest tests/niri/ -v
```

Expected: All tests pass

**Step 2: Reinstall package**

```bash
uv sync
```

**Step 3: Manual verification**

```bash
# Verify entry points are installed
which wlr-niri-track-terminals
which wlr-niri-restore-tmux
which wlr-niri-restore-mosh

# Run tracker manually
wlr-niri-track-terminals

# Check storage was created
cat ~/.local/state/niri/tmux.json
```

**Step 4: Final commit**

```bash
git add -A
git commit -m "docs: update documentation for unified niri tracking"
```

---

## Summary

This implementation plan creates a unified Python module for window tracking with:

- **8 source files** in `src/wlrenv/niri/`
- **8 test files** in `tests/niri/`
- **4 CLI entry points** installed via pyproject.toml
- **~500 lines of Python** replacing ~300 lines of fragmented bash

The module handles tmux, mosh, and Librewolf with a common storage layer and app-specific identification logic.
