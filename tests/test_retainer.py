import re
from datetime import UTC, datetime, timedelta
from itertools import islice
from pathlib import Path
from unittest.mock import patch

import pytest

from wlrenv.retainer import (
    DEFAULT_EXPONENTIAL_FACTOR,
    DEFAULT_FIRST_BUCKET_DAYS,
    DEFAULT_MAX_BUCKET_DAYS,
    DEFAULT_PRESERVE_DAYS,
    TimestampedFile,
    _gen_buckets,
    _get_elements_to_delete,
    _group_deletable_by_buckets,
    delete_files,
    get_files_to_delete,
    list_matching_files,
    main,
)

# Default retention parameters as timedeltas for tests
PRESERVE_WINDOW = timedelta(days=DEFAULT_PRESERVE_DAYS)
FIRST_BUCKET = timedelta(days=DEFAULT_FIRST_BUCKET_DAYS)
MAX_BUCKET = timedelta(days=DEFAULT_MAX_BUCKET_DAYS)
EXP_FACTOR = DEFAULT_EXPONENTIAL_FACTOR


# =============================================================================
# Core algorithm tests
# =============================================================================


def test_gen_buckets() -> None:
    """Test that bucket start times are generated correctly."""
    end_of_newest_bucket = datetime(year=2020, month=6, day=27)
    expected = [
        datetime(year=2020, month=6, day=26),
        datetime(year=2020, month=6, day=24),
        datetime(year=2020, month=6, day=20),
        datetime(year=2020, month=6, day=12),
        datetime(year=2020, month=5, day=27),
        datetime(year=2020, month=4, day=25),
        datetime(year=2020, month=3, day=24),
        datetime(year=2020, month=2, day=21),
        datetime(year=2020, month=1, day=20),
        datetime(year=2019, month=12, day=19),
    ]
    actual = list(
        islice(
            _gen_buckets(end_of_newest_bucket, FIRST_BUCKET, MAX_BUCKET, EXP_FACTOR), 10
        )
    )
    assert expected == actual


def test_gen_buckets_max_bucket_size() -> None:
    """Test that _gen_buckets respects max bucket size."""
    start = datetime(2000, 1, 1)
    buckets = list(
        islice(_gen_buckets(start, FIRST_BUCKET, MAX_BUCKET, EXP_FACTOR), 20)
    )

    # Check that bucket sizes don't exceed max
    for i in range(1, len(buckets)):
        delta = buckets[i - 1] - buckets[i]
        assert delta.days <= DEFAULT_MAX_BUCKET_DAYS


def test_group_deletable_by_buckets() -> None:
    """Test grouping elements into time buckets."""
    start_of_preserved_window = datetime(year=2020, month=6, day=27)
    elements: set[datetime] = {
        datetime(year=2020, month=6, day=27),  # preserved
        datetime(year=2020, month=6, day=26),  # first bucket
        datetime(year=2020, month=6, day=25),  # second bucket
        datetime(year=2020, month=6, day=24),  # second bucket
        datetime(year=2020, month=6, day=23),  # third bucket
        datetime(year=2020, month=6, day=10),  # fifth bucket
    }
    expected = [
        {datetime(year=2020, month=6, day=26)},
        {datetime(year=2020, month=6, day=25), datetime(year=2020, month=6, day=24)},
        {datetime(year=2020, month=6, day=23)},
        set(),
        {datetime(year=2020, month=6, day=10)},
    ]
    actual = list(
        _group_deletable_by_buckets(
            start_of_preserved_window,
            elements,
            lambda x: x,
            FIRST_BUCKET,
            MAX_BUCKET,
            EXP_FACTOR,
        )
    )
    assert expected == actual


def test_group_deletable_by_buckets_empty_set() -> None:
    """Test _group_deletable_by_buckets with empty set."""
    start_of_preserved_window = datetime(2020, 6, 27)
    elements: set[datetime] = set()
    result = list(
        _group_deletable_by_buckets(
            start_of_preserved_window,
            elements,
            lambda x: x,
            FIRST_BUCKET,
            MAX_BUCKET,
            EXP_FACTOR,
        )
    )
    assert result == [set()]


def test_group_deletable_by_buckets_all_in_preserved_window() -> None:
    """Test when all elements are in preserved window."""
    start_of_preserved_window = datetime(2020, 6, 27)
    elements = {
        datetime(2020, 6, 27),  # exactly at window start
        datetime(2020, 6, 28),  # after window start
    }
    result = list(
        _group_deletable_by_buckets(
            start_of_preserved_window,
            elements,
            lambda x: x,
            FIRST_BUCKET,
            MAX_BUCKET,
            EXP_FACTOR,
        )
    )
    assert result == [set()]


def test_get_elements_to_delete_empty_set() -> None:
    """Test _get_elements_to_delete with empty set."""
    effective_date = datetime(2020, 6, 27)
    elements: set[datetime] = set()
    result = _get_elements_to_delete(
        effective_date,
        elements,
        lambda x: x,
        PRESERVE_WINDOW,
        FIRST_BUCKET,
        MAX_BUCKET,
        EXP_FACTOR,
    )
    assert result == set()


def test_get_elements_to_delete_all_preserved() -> None:
    """Test when all elements are in preserved window."""
    effective_date = datetime(2020, 6, 27)
    elements = {
        datetime(2020, 6, 26),  # within 7 days
        datetime(2020, 6, 25),  # within 7 days
        datetime(2020, 6, 21),  # within 7 days
    }
    result = _get_elements_to_delete(
        effective_date,
        elements,
        lambda x: x,
        PRESERVE_WINDOW,
        FIRST_BUCKET,
        MAX_BUCKET,
        EXP_FACTOR,
    )
    assert result == set()


def test_get_elements_to_delete_single_element_per_bucket() -> None:
    """Test when each bucket has one element (all preserved)."""
    effective_date = datetime(2020, 6, 27)
    elements = {
        datetime(2020, 6, 19),  # oldest in first bucket (8 days ago)
        datetime(2020, 6, 17),  # oldest in second bucket (10 days ago)
        datetime(2020, 6, 13),  # oldest in third bucket (14 days ago)
    }
    result = _get_elements_to_delete(
        effective_date,
        elements,
        lambda x: x,
        PRESERVE_WINDOW,
        FIRST_BUCKET,
        MAX_BUCKET,
        EXP_FACTOR,
    )
    assert result == set()  # Keep oldest in each bucket


def test_get_elements_to_delete_multiple_per_bucket() -> None:
    """Test with multiple elements per bucket."""
    effective_date = datetime(2020, 6, 27)
    elements = {
        datetime(2020, 6, 19),  # oldest in bucket - keep
        datetime(2020, 6, 19, 12),  # newer in same bucket - delete
        datetime(2020, 6, 19, 18),  # newest in same bucket - delete
        datetime(2020, 6, 17),  # oldest in different bucket - keep
    }
    result = _get_elements_to_delete(
        effective_date,
        elements,
        lambda x: x,
        PRESERVE_WINDOW,
        FIRST_BUCKET,
        MAX_BUCKET,
        EXP_FACTOR,
    )
    expected = {
        datetime(2020, 6, 19, 12),
        datetime(2020, 6, 19, 18),
    }
    assert result == expected


# =============================================================================
# Simulation test
# =============================================================================


def _simulate_additions_and_deletions(
    first_addition: datetime,
    addition_frequency: timedelta,
    first_deletion_run: datetime,
    deletion_frequency: timedelta,
    simulation_end: datetime,
) -> set[datetime]:
    """Simulate regular additions and periodic retention runs."""
    current_elements: set[datetime] = set()
    next_addition = first_addition
    next_deletion_run = first_deletion_run
    while next_addition < simulation_end or next_deletion_run < simulation_end:
        if next_addition < next_deletion_run:
            current_elements.add(next_addition)
            next_addition += addition_frequency
        else:
            elements_to_delete = _get_elements_to_delete(
                next_deletion_run,
                current_elements,
                lambda x: x,
                PRESERVE_WINDOW,
                FIRST_BUCKET,
                MAX_BUCKET,
                EXP_FACTOR,
            )
            current_elements -= elements_to_delete
            next_deletion_run += deletion_frequency
    return current_elements


def test_simulate_additions_and_deletions() -> None:
    """Test long-term simulation of retention behavior."""
    actual = _simulate_additions_and_deletions(
        first_addition=datetime(year=2020, month=6, day=27, hour=1),
        addition_frequency=timedelta(hours=6),
        first_deletion_run=datetime(year=2020, month=6, day=1),
        deletion_frequency=timedelta(hours=1),
        simulation_end=datetime(year=2022, month=6, day=27, hour=23),
    )
    expected = {
        datetime(year=2020, month=6, day=27, hour=1),
        datetime(year=2020, month=7, day=29, hour=1),
        datetime(year=2020, month=8, day=30, hour=1),
        datetime(year=2020, month=10, day=1, hour=1),
        datetime(year=2020, month=11, day=2, hour=1),
        datetime(year=2020, month=12, day=4, hour=1),
        datetime(year=2021, month=1, day=5, hour=1),
        datetime(year=2021, month=2, day=6, hour=1),
        datetime(year=2021, month=3, day=10, hour=1),
        datetime(year=2021, month=4, day=11, hour=1),
        datetime(year=2021, month=5, day=13, hour=1),
        datetime(year=2021, month=6, day=14, hour=1),
        datetime(year=2021, month=7, day=16, hour=1),
        datetime(year=2021, month=8, day=17, hour=1),
        datetime(year=2021, month=9, day=18, hour=1),
        datetime(year=2021, month=10, day=20, hour=1),
        datetime(year=2021, month=11, day=21, hour=1),
        datetime(year=2021, month=12, day=23, hour=1),
        datetime(year=2022, month=1, day=24, hour=1),
        datetime(year=2022, month=2, day=25, hour=1),
        datetime(year=2022, month=3, day=29, hour=1),
        datetime(year=2022, month=4, day=30, hour=1),
        datetime(year=2022, month=6, day=1, hour=1),
        datetime(year=2022, month=6, day=9, hour=1),
        datetime(year=2022, month=6, day=17, hour=1),
        datetime(year=2022, month=6, day=19, hour=1),
        datetime(year=2022, month=6, day=20, hour=1),
        datetime(year=2022, month=6, day=21, hour=1),
        datetime(year=2022, month=6, day=21, hour=7),
        datetime(year=2022, month=6, day=21, hour=13),
        datetime(year=2022, month=6, day=21, hour=19),
        datetime(year=2022, month=6, day=22, hour=1),
        datetime(year=2022, month=6, day=22, hour=7),
        datetime(year=2022, month=6, day=22, hour=13),
        datetime(year=2022, month=6, day=22, hour=19),
        datetime(year=2022, month=6, day=23, hour=1),
        datetime(year=2022, month=6, day=23, hour=7),
        datetime(year=2022, month=6, day=23, hour=13),
        datetime(year=2022, month=6, day=23, hour=19),
        datetime(year=2022, month=6, day=24, hour=1),
        datetime(year=2022, month=6, day=24, hour=7),
        datetime(year=2022, month=6, day=24, hour=13),
        datetime(year=2022, month=6, day=24, hour=19),
        datetime(year=2022, month=6, day=25, hour=1),
        datetime(year=2022, month=6, day=25, hour=7),
        datetime(year=2022, month=6, day=25, hour=13),
        datetime(year=2022, month=6, day=25, hour=19),
        datetime(year=2022, month=6, day=26, hour=1),
        datetime(year=2022, month=6, day=26, hour=7),
        datetime(year=2022, month=6, day=26, hour=13),
        datetime(year=2022, month=6, day=26, hour=19),
        datetime(year=2022, month=6, day=27, hour=1),
        datetime(year=2022, month=6, day=27, hour=7),
        datetime(year=2022, month=6, day=27, hour=13),
        datetime(year=2022, month=6, day=27, hour=19),
    }
    assert expected == actual


# =============================================================================
# File operations tests
# =============================================================================


def test_timestamped_file_namedtuple() -> None:
    """Test TimestampedFile NamedTuple."""
    path = Path("/test/file.txt")
    timestamp = datetime(2020, 6, 27, tzinfo=UTC)
    file = TimestampedFile(path=path, timestamp=timestamp)
    assert file.path == path
    assert file.timestamp == timestamp


def test_list_matching_files(tmp_path: Path) -> None:
    """Test listing files matching a pattern."""
    # Create test files
    (tmp_path / "pane_contents.2020-06-27T120000.tar.gz").touch()
    (tmp_path / "pane_contents.2020-06-26T133000.tar.gz").touch()
    (tmp_path / "other_file.txt").touch()
    (tmp_path / "subdir").mkdir()

    pattern = re.compile(
        r'pane_contents\.(?P<timestamp>\d{4}-\d{2}-\d{2}T\d{6})\.tar\.gz'
    )
    timestamp_format = '%Y-%m-%dT%H%M%S'

    files = list_matching_files(tmp_path, pattern, timestamp_format)

    assert len(files) == 2
    timestamps = {f.timestamp for f in files}
    assert datetime(2020, 6, 27, 12, 0, 0, tzinfo=UTC) in timestamps
    assert datetime(2020, 6, 26, 13, 30, 0, tzinfo=UTC) in timestamps


def test_list_matching_files_nonexistent_dir() -> None:
    """Test list_matching_files with nonexistent directory."""
    pattern = re.compile(r'.*')
    with pytest.raises(ValueError, match="Directory does not exist"):
        list_matching_files(Path("/nonexistent/dir"), pattern, '%Y')


def test_list_matching_files_empty_dir(tmp_path: Path) -> None:
    """Test list_matching_files with empty directory."""
    pattern = re.compile(
        r'pane_contents\.(?P<timestamp>\d{4}-\d{2}-\d{2}T\d{6})\.tar\.gz'
    )
    files = list_matching_files(tmp_path, pattern, '%Y-%m-%dT%H%M%S')
    assert files == []


def test_get_files_to_delete(tmp_path: Path) -> None:
    """Test determining which files to delete."""
    # Create files spanning multiple buckets
    effective_date = datetime(2020, 6, 27, tzinfo=UTC)

    # Files in preservation window (keep all)
    (tmp_path / "pane_contents.2020-06-26T120000.tar.gz").touch()
    (tmp_path / "pane_contents.2020-06-25T120000.tar.gz").touch()

    # Files in first bucket after window (keep oldest)
    (tmp_path / "pane_contents.2020-06-19T120000.tar.gz").touch()  # keep
    (tmp_path / "pane_contents.2020-06-19T180000.tar.gz").touch()  # delete

    pattern = re.compile(
        r'pane_contents\.(?P<timestamp>\d{4}-\d{2}-\d{2}T\d{6})\.tar\.gz'
    )

    files_to_delete = get_files_to_delete(
        tmp_path,
        pattern,
        '%Y-%m-%dT%H%M%S',
        effective_date=effective_date,
    )

    assert len(files_to_delete) == 1
    assert tmp_path / "pane_contents.2020-06-19T180000.tar.gz" in files_to_delete


def test_delete_files_dry_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Test delete_files in dry run mode."""
    file1 = tmp_path / "file1.txt"
    file2 = tmp_path / "file2.txt"
    file1.touch()
    file2.touch()

    delete_files({file1, file2}, dry_run=True)

    # Files should still exist
    assert file1.exists()
    assert file2.exists()

    # Should print dry run messages
    captured = capsys.readouterr()
    assert "(dry run) would delete:" in captured.out


def test_delete_files_actual(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Test delete_files actually deletes files."""
    file1 = tmp_path / "file1.txt"
    file2 = tmp_path / "file2.txt"
    file1.touch()
    file2.touch()

    delete_files({file1, file2}, dry_run=False)

    # Files should be deleted
    assert not file1.exists()
    assert not file2.exists()

    # Should print delete messages
    captured = capsys.readouterr()
    assert "deleting:" in captured.out


# =============================================================================
# CLI tests
# =============================================================================


def test_main_dry_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Test main function with dry run."""
    # Create test files
    (tmp_path / "pane_contents.2020-06-19T120000.tar.gz").touch()
    (tmp_path / "pane_contents.2020-06-19T180000.tar.gz").touch()

    with patch('wlrenv.retainer.datetime') as mock_datetime:
        mock_datetime.now.return_value = datetime(2020, 6, 27, tzinfo=UTC)
        mock_datetime.strptime = datetime.strptime

        result = main(
            [
                '--dir',
                str(tmp_path),
                '--pattern',
                r'pane_contents\.(?P<timestamp>\d{4}-\d{2}-\d{2}T\d{6})\.tar\.gz',
                '--format',
                '%Y-%m-%dT%H%M%S',
                '--dry-run',
            ]
        )

    assert result == 0
    # Files should still exist (dry run)
    assert (tmp_path / "pane_contents.2020-06-19T120000.tar.gz").exists()
    assert (tmp_path / "pane_contents.2020-06-19T180000.tar.gz").exists()


def test_main_no_files_to_delete(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Test main when no files need deletion."""
    result = main(
        [
            '--dir',
            str(tmp_path),
            '--pattern',
            r'pane_contents\.(?P<timestamp>\d{4}-\d{2}-\d{2}T\d{6})\.tar\.gz',
            '--format',
            '%Y-%m-%dT%H%M%S',
        ]
    )

    assert result == 0
    captured = capsys.readouterr()
    assert "No files to delete" in captured.out


def test_main_invalid_pattern(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Test main with invalid regex pattern."""
    result = main(
        [
            '--dir',
            str(tmp_path),
            '--pattern',
            '[invalid',
            '--format',
            '%Y',
        ]
    )

    assert result == 1
    captured = capsys.readouterr()
    assert "Invalid regex pattern" in captured.err


def test_main_missing_timestamp_group(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Test main when pattern lacks timestamp group."""
    result = main(
        [
            '--dir',
            str(tmp_path),
            '--pattern',
            r'.*\.tar\.gz',
            '--format',
            '%Y',
        ]
    )

    assert result == 1
    captured = capsys.readouterr()
    assert 'named group "timestamp"' in captured.err


def test_main_nonexistent_dir(capsys: pytest.CaptureFixture[str]) -> None:
    """Test main with nonexistent directory."""
    result = main(
        [
            '--dir',
            '/nonexistent/path',
            '--pattern',
            r'(?P<timestamp>\d+)',
            '--format',
            '%Y',
        ]
    )

    assert result == 1
    captured = capsys.readouterr()
    assert "Directory does not exist" in captured.err


def test_main_custom_retention_params(tmp_path: Path) -> None:
    """Test main with custom retention parameters."""
    (tmp_path / "file.2020-06-26T120000.txt").touch()

    result = main(
        [
            '--dir',
            str(tmp_path),
            '--pattern',
            r'file\.(?P<timestamp>\d{4}-\d{2}-\d{2}T\d{6})\.txt',
            '--format',
            '%Y-%m-%dT%H%M%S',
            '--preserve-days',
            '14',
            '--first-bucket-days',
            '2',
            '--max-bucket-days',
            '64',
            '--exponential-factor',
            '3',
            '--dry-run',
        ]
    )

    assert result == 0
