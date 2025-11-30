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
