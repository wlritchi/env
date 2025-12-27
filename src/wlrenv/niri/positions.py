"""Boot-keyed window position storage with upsert semantics."""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
import uuid as uuid_lib
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import TextIO

import cattrs
from attrs import Factory, define

from wlrenv.niri import config


@define
class PositionEntry:
    """A single window position entry."""

    id: str
    index: int
    window_id: int
    width: int


@define
class BootData:
    """Position data for a single boot session."""

    updated_at: str
    apps: list[str] = Factory(list)
    workspaces: dict[str, list[PositionEntry]] = Factory(dict)


@define
class PositionsFile:
    """Root structure for positions.json."""

    version: int = 1
    boots: dict[str, BootData] = Factory(dict)


# Configure cattrs converter
_converter = cattrs.Converter()


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


def _get_positions_path() -> Path:
    """Get path to positions.json."""
    return config.STATE_DIR / "positions.json"


def load_positions() -> PositionsFile:
    """Load positions data, returning empty structure if missing."""
    path = _get_positions_path()
    if path.exists():
        with open(path) as f:
            data = json.load(f)
            return _converter.structure(data, PositionsFile)
    return PositionsFile()


def save_positions(data: PositionsFile) -> None:
    """Atomically save positions data."""
    path = _get_positions_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    # Write to temp file, then atomic rename
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".json.tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(_converter.unstructure(data), f, indent=2)
        os.rename(tmp_path, path)
    except BaseException:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


class PositionsLock:
    """Context manager for exclusive access to positions.json."""

    def __init__(self) -> None:
        self._lock_path = config.STATE_DIR / "positions.lock"
        self._lock_file: TextIO | None = None

    def __enter__(self) -> PositionsLock:
        config.STATE_DIR.mkdir(parents=True, exist_ok=True)
        self._lock_file = open(self._lock_path, "w")
        fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if self._lock_file:
            fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
            self._lock_file.close()


def upsert_entries(
    app: str,
    workspace_id: int,
    entries: list[PositionEntry],
) -> None:
    """Upsert position entries for an app into current boot."""
    with PositionsLock():
        data = load_positions()
        boot_id = get_boot_id()
        ws_key = str(workspace_id)

        # Ensure boot exists
        if boot_id not in data.boots:
            data.boots[boot_id] = BootData(updated_at="")

        boot = data.boots[boot_id]

        # Add app to set
        if app not in boot.apps:
            boot.apps.append(app)

        # Remove any existing entries with same stable IDs (from any workspace)
        entry_ids = {e.id for e in entries}
        for ws, ws_entries in boot.workspaces.items():
            boot.workspaces[ws] = [e for e in ws_entries if e.id not in entry_ids]

        # Clean up empty workspaces
        boot.workspaces = {k: v for k, v in boot.workspaces.items() if v}

        # Add new entries
        if ws_key not in boot.workspaces:
            boot.workspaces[ws_key] = []
        boot.workspaces[ws_key].extend(entries)

        # Update timestamp
        boot.updated_at = datetime.now(UTC).isoformat()

        save_positions(data)


def prune_dominated_boots(current_boot_id: str) -> None:
    """Remove boots dominated by the current boot.

    Boot A dominates Boot B if:
    1. A.apps is a superset of B.apps
    2. A.updated_at > B.updated_at
    """
    with PositionsLock():
        data = load_positions()

        if current_boot_id not in data.boots:
            return

        current = data.boots[current_boot_id]
        current_apps = set(current.apps)
        current_time = current.updated_at

        to_delete = []
        for boot_id, boot in data.boots.items():
            if boot_id == current_boot_id:
                continue
            boot_apps = set(boot.apps)
            # Current dominates boot if current has all of boot's apps and is newer
            if boot_apps <= current_apps and boot.updated_at < current_time:
                to_delete.append(boot_id)

        for boot_id in to_delete:
            del data.boots[boot_id]

        if to_delete:
            save_positions(data)


@define
class PositionResult:
    """Result from looking up a position."""

    workspace_id: int
    width: int


def lookup_latest_position(stable_id: str) -> PositionResult | None:
    """Find the most recent position for a stable ID.

    Returns PositionResult with workspace_id and width, or None if not found.
    """
    data = load_positions()

    latest_time: str | None = None
    latest_result: PositionResult | None = None

    for _boot_id, boot in data.boots.items():
        for ws_key, entries in boot.workspaces.items():
            for entry in entries:
                if entry.id == stable_id:
                    if latest_time is None or boot.updated_at > latest_time:
                        latest_time = boot.updated_at
                        latest_result = PositionResult(
                            workspace_id=int(ws_key),
                            width=entry.width,
                        )

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
        best_boot: BootData | None = None
        best_time: str | None = None

        for _boot_id, boot in data.boots.items():
            if this_app not in boot.apps or other_app not in boot.apps:
                continue
            if ws_key not in boot.workspaces:
                continue

            # Check if stable_id exists in this workspace
            entries = boot.workspaces[ws_key]
            if not any(e.id == stable_id for e in entries):
                continue

            if best_time is None or boot.updated_at > best_time:
                best_time = boot.updated_at
                best_boot = boot

        if best_boot is None:
            continue

        # Find this_id's index and collect predecessors
        entries = best_boot.workspaces[ws_key]
        this_index: int | None = None
        for entry in entries:
            if entry.id == stable_id:
                this_index = entry.index
                break

        if this_index is not None:
            for entry in entries:
                if entry.index < this_index and entry.id != stable_id:
                    predecessors.add(entry.id)

    return list(predecessors)


def resolve_predecessors_to_window_ids(
    predecessor_ids: list[str],
    workspace_id: int,
) -> list[int]:
    """Resolve predecessor stable IDs to window IDs in current boot."""
    data = load_positions()
    boot_id = get_boot_id()
    ws_key = str(workspace_id)

    if boot_id not in data.boots:
        return []

    boot = data.boots[boot_id]
    if ws_key not in boot.workspaces:
        return []

    entries = boot.workspaces[ws_key]
    id_to_window: dict[str, int] = {e.id: e.window_id for e in entries}

    return [id_to_window[pid] for pid in predecessor_ids if pid in id_to_window]
