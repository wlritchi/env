# tests/niri/test_ordering.py
from __future__ import annotations


def test_get_predecessors_returns_empty_for_first_in_order() -> None:
    from wlrenv.niri.ordering import get_predecessors

    preds = get_predecessors("tmux:a", ["tmux:a", "tmux:b", "tmux:c"])

    assert preds == []


def test_get_predecessors_returns_all_before_identity() -> None:
    from wlrenv.niri.ordering import get_predecessors

    preds = get_predecessors("tmux:c", ["tmux:a", "tmux:b", "tmux:c"])

    assert preds == ["tmux:a", "tmux:b"]


def test_get_predecessors_returns_empty_for_unknown_identity() -> None:
    from wlrenv.niri.ordering import get_predecessors

    preds = get_predecessors("tmux:unknown", ["tmux:a", "tmux:b"])

    assert preds == []
