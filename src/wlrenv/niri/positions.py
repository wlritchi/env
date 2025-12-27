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
from typing import Any

from wlrenv.niri import config


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
        self._lock_path = config.STATE_DIR / "positions.lock"
        self._lock_file: Any = None

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
        boot["updated_at"] = datetime.now(UTC).isoformat()

        save_positions(data)


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
