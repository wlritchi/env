"""Persistent storage for window metadata."""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any

from wlrenv.niri.config import get_storage_path


def _load(app: str) -> dict[str, Any]:
    """Load storage data for app, returning empty structure if missing."""
    path = get_storage_path(app)
    if path.exists():
        return json.loads(path.read_text())  # type: ignore[no-any-return]
    return {"version": 1, "entries": {}}


def _save(app: str, data: dict[str, Any]) -> None:
    """Atomically save storage data for app."""
    path = get_storage_path(app)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Write to temp file, then atomic rename
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".json.tmp")
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
