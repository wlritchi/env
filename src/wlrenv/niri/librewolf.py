# src/wlrenv/niri/librewolf.py
"""Librewolf window identification via URL matching."""

from __future__ import annotations

import json
import os
import tempfile
import uuid as uuid_lib
from pathlib import Path
from typing import Any

from wlrenv.niri import config


def _get_identities_path() -> Path:
    return config.STATE_DIR / "librewolf-identities.json"


class UrlMatcher:
    """Stateful URL matcher for Librewolf window identification."""

    def __init__(self, entries: list[dict[str, Any]]) -> None:
        self.entries = entries
        self.available: set[int] = set(range(len(entries)))

    @classmethod
    def load(cls) -> UrlMatcher:
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

        # First try to find best match in available pool
        for i in self.available:
            entry_urls = set(self.entries[i]["urls"])
            overlap = len(url_set & entry_urls)
            if overlap > best_overlap:
                best_idx = i
                best_overlap = overlap

        if best_idx is not None and best_overlap > 0:
            self.available.remove(best_idx)
            # Update stored URLs to current
            self.entries[best_idx]["urls"] = urls
            return self.entries[best_idx]["uuid"]  # type: ignore[no-any-return]

        # No good match in available pool - try any available entry
        if self.available:
            # Just take the first available entry if no overlap found
            first_available = min(self.available)
            self.available.remove(first_available)
            self.entries[first_available]["urls"] = urls
            return self.entries[first_available]["uuid"]  # type: ignore[no-any-return]

        # No available entries - create new entry
        new_uuid = str(uuid_lib.uuid4())
        self.entries.append({"uuid": new_uuid, "urls": urls})
        return new_uuid

    def save(self) -> None:
        """Persist matcher state to disk."""
        config.STATE_DIR.mkdir(parents=True, exist_ok=True)
        path = _get_identities_path()

        data = {"version": 1, "entries": self.entries}

        # Atomic write
        fd, tmp_path = tempfile.mkstemp(dir=config.STATE_DIR, suffix=".json.tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2)
            os.rename(tmp_path, path)
        except BaseException:
            os.unlink(tmp_path)
            raise
