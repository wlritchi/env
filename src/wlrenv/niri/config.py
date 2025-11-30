"""Configuration and paths for niri window tracking."""

from pathlib import Path

STATE_DIR = Path.home() / ".local" / "state" / "niri"


def get_storage_path(app: str) -> Path:
    """Return path to storage JSON for given app."""
    return STATE_DIR / f"{app}.json"
