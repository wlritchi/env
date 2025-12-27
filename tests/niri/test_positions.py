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
                    "1": [
                        {
                            "id": "tmux:dotfiles",
                            "index": 1,
                            "window_id": 100,
                            "width": 50,
                        }
                    ]
                },
            }
        },
    }

    save_positions(data)
    loaded = load_positions()

    assert loaded == data


def test_save_is_atomic(temp_dirs: tuple[Path, Path]) -> None:
    from wlrenv.niri.positions import save_positions

    state_dir, _ = temp_dirs

    # Save initial data
    save_positions({"version": 1, "boots": {"a": {"apps": []}}})

    # Check no temp files left behind
    files = list(state_dir.glob("*.tmp"))
    assert files == []

    # File exists
    assert (state_dir / "positions.json").exists()
