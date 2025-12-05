"""Storage for workspace column ordering."""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any

from wlrenv.niri.config import STATE_DIR


def _get_orders_path() -> str:
    return str(STATE_DIR / "orders.json")


def _load() -> dict[str, Any]:
    """Load orders data, returning empty structure if missing."""
    path = _get_orders_path()
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)  # type: ignore[no-any-return]
    return {"version": 1, "workspaces": {}}


def _save(data: dict[str, Any]) -> None:
    """Atomically save orders data."""
    path = _get_orders_path()
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    # Write to temp file, then atomic rename
    fd, tmp_path = tempfile.mkstemp(dir=STATE_DIR, suffix=".json.tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.rename(tmp_path, path)
    except BaseException:
        os.unlink(tmp_path)
        raise


def get_order(workspace_id: int) -> list[str]:
    """Get ordered list of window identities for workspace."""
    data = _load()
    return data["workspaces"].get(str(workspace_id), [])


def save_order(workspace_id: int, order: list[str]) -> None:
    """Save ordered list of window identities for workspace."""
    data = _load()
    data["workspaces"][str(workspace_id)] = order
    _save(data)
