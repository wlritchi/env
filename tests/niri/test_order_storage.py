# tests/niri/test_order_storage.py
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def temp_state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from wlrenv.niri import config

    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    return tmp_path


def test_get_order_returns_empty_list_for_missing_workspace(
    temp_state_dir: Path,
) -> None:
    from wlrenv.niri.order_storage import get_order

    order = get_order(workspace_id=1)

    assert order == []


def test_save_order_persists_data(temp_state_dir: Path) -> None:
    from wlrenv.niri.order_storage import get_order, save_order

    save_order(workspace_id=2, order=["tmux:dotfiles", "mosh:server:main"])

    order = get_order(workspace_id=2)
    assert order == ["tmux:dotfiles", "mosh:server:main"]


def test_save_order_overwrites_existing(temp_state_dir: Path) -> None:
    from wlrenv.niri.order_storage import get_order, save_order

    save_order(workspace_id=2, order=["a", "b"])
    save_order(workspace_id=2, order=["c", "d", "e"])

    order = get_order(workspace_id=2)
    assert order == ["c", "d", "e"]


def test_orders_are_per_workspace(temp_state_dir: Path) -> None:
    from wlrenv.niri.order_storage import get_order, save_order

    save_order(workspace_id=1, order=["a"])
    save_order(workspace_id=2, order=["b", "c"])

    assert get_order(workspace_id=1) == ["a"]
    assert get_order(workspace_id=2) == ["b", "c"]
