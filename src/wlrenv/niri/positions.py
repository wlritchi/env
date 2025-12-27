"""Boot-keyed window position storage with upsert semantics."""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
import uuid as uuid_lib
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
