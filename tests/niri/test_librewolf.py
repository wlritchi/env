# tests/niri/test_librewolf.py
from __future__ import annotations

from pathlib import Path

import pytest

from wlrenv.niri.librewolf import UrlMatcher


@pytest.fixture
def temp_state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Use temporary directory for state."""
    import wlrenv.niri.config as config

    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    return tmp_path


def test_matcher_creates_new_uuid_for_unknown_urls(temp_state_dir: Path) -> None:
    matcher = UrlMatcher.load()

    uuid = matcher.match_or_create(["https://example.com"])

    assert uuid is not None
    assert len(uuid) == 36  # UUID format


def test_matcher_returns_same_uuid_for_same_urls(temp_state_dir: Path) -> None:
    matcher = UrlMatcher.load()

    uuid1 = matcher.match_or_create(["https://example.com", "https://test.com"])
    matcher.save()

    # Reload and match again
    matcher2 = UrlMatcher.load()
    uuid2 = matcher2.match_or_create(["https://example.com", "https://test.com"])

    assert uuid1 == uuid2


def test_matcher_matches_by_overlap(temp_state_dir: Path) -> None:
    matcher = UrlMatcher.load()

    # Create entry with some URLs
    uuid1 = matcher.match_or_create(["https://a.com", "https://b.com", "https://c.com"])
    matcher.save()

    # Match with partial overlap (2 of 3)
    matcher2 = UrlMatcher.load()
    uuid2 = matcher2.match_or_create(
        ["https://a.com", "https://b.com", "https://d.com"]
    )

    assert uuid1 == uuid2


def test_matcher_removes_matched_from_pool(temp_state_dir: Path) -> None:
    matcher = UrlMatcher.load()

    # Create two entries
    uuid1 = matcher.match_or_create(["https://a.com"])
    uuid2 = matcher.match_or_create(["https://b.com"])
    matcher.save()

    # In a new session, match first one - should remove from pool
    matcher2 = UrlMatcher.load()
    result1 = matcher2.match_or_create(["https://a.com"])
    result2 = matcher2.match_or_create(
        ["https://a.com"]
    )  # Same URLs, but first is taken

    assert result1 == uuid1
    assert result2 == uuid2  # Falls back to second entry or creates new


def test_matcher_updates_urls_on_match(temp_state_dir: Path) -> None:
    matcher = UrlMatcher.load()

    matcher.match_or_create(["https://old.com"])
    matcher.save()

    # Match with different URLs
    matcher2 = UrlMatcher.load()
    matcher2.match_or_create(["https://new.com"])
    matcher2.save()

    # Verify URLs were updated
    matcher3 = UrlMatcher.load()
    # The entry should now have the new URL
    assert matcher3.entries[0]["urls"] == ["https://new.com"]
