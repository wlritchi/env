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
