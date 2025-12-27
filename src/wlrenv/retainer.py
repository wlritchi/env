#!/usr/bin/env python3
"""
Retainer: Prune old files using exponential bucket retention.

Keeps all files within a preservation window (default 7 days), then thins
older files by keeping only the oldest file in each exponentially-growing
time bucket.
"""

import re
import sys
from argparse import ArgumentParser
from collections.abc import Callable, Generator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import NamedTuple, TypeVar

# Default retention parameters
DEFAULT_PRESERVE_DAYS = 7
DEFAULT_FIRST_BUCKET_DAYS = 1
DEFAULT_MAX_BUCKET_DAYS = 32
DEFAULT_EXPONENTIAL_FACTOR = 2

_T = TypeVar('_T')


# =============================================================================
# Core retention algorithm
# =============================================================================


def _gen_buckets(
    start_of_preserved_window: datetime,
    first_bucket_size: timedelta,
    max_bucket_size: timedelta,
    exponential_factor: int,
) -> Iterator[datetime]:
    """Generate start times of exponentially-growing time buckets going backwards."""
    bucket_size = first_bucket_size
    start_of_bucket = start_of_preserved_window
    while True:
        start_of_bucket -= bucket_size
        yield start_of_bucket
        bucket_size *= exponential_factor
        if bucket_size > max_bucket_size:
            bucket_size = max_bucket_size


def _group_deletable_by_buckets(
    start_of_preserved_window: datetime,
    deletable_elements: set[_T],
    get_element_timestamp: Callable[[_T], datetime],
    first_bucket_size: timedelta,
    max_bucket_size: timedelta,
    exponential_factor: int,
) -> Generator[set[_T], None, None]:
    """Group elements older than the preserved window into time buckets."""
    deletable_elements = {
        el
        for el in deletable_elements
        if get_element_timestamp(el) < start_of_preserved_window
    }
    bucket_gen = _gen_buckets(
        start_of_preserved_window,
        first_bucket_size,
        max_bucket_size,
        exponential_factor,
    )
    for bucket_start in bucket_gen:
        els_in_bucket = {
            el for el in deletable_elements if get_element_timestamp(el) >= bucket_start
        }
        deletable_elements -= els_in_bucket
        yield els_in_bucket
        if not deletable_elements:
            break


def _get_elements_to_delete(
    effective_date: datetime,
    elements: set[_T],
    get_element_timestamp: Callable[[_T], datetime],
    preserve_window: timedelta,
    first_bucket_size: timedelta,
    max_bucket_size: timedelta,
    exponential_factor: int,
) -> set[_T]:
    """
    Determine which elements should be deleted.

    Keeps all elements within preserve_window, then keeps only the oldest
    element in each exponentially-growing time bucket.
    """
    start_of_preserved_window = effective_date - preserve_window
    deletable_by_buckets = _group_deletable_by_buckets(
        start_of_preserved_window,
        elements,
        get_element_timestamp,
        first_bucket_size,
        max_bucket_size,
        exponential_factor,
    )
    elements_to_delete: set[_T] = set()
    for bucket in deletable_by_buckets:
        sorted_bucket = sorted(bucket, key=get_element_timestamp)
        # keep earliest element in bucket
        elements_to_delete |= set(sorted_bucket[1:])
    return elements_to_delete


# =============================================================================
# File operations
# =============================================================================


class TimestampedFile(NamedTuple):
    path: Path
    timestamp: datetime


def list_matching_files(
    directory: Path,
    pattern: re.Pattern[str],
    timestamp_format: str,
) -> list[TimestampedFile]:
    """
    List files in directory matching pattern, extracting timestamps.

    The pattern must have a named group 'timestamp' that captures the
    timestamp portion of the filename.
    """
    files: list[TimestampedFile] = []
    if not directory.is_dir():
        raise ValueError(f"Directory does not exist: {directory}")

    for path in directory.iterdir():
        if not path.is_file():
            continue
        match = pattern.fullmatch(path.name)
        if match:
            timestamp_str = match.group('timestamp')
            if not timestamp_str:
                continue
            timestamp = datetime.strptime(timestamp_str, timestamp_format).replace(
                tzinfo=UTC
            )
            files.append(TimestampedFile(path=path, timestamp=timestamp))
    return files


def get_files_to_delete(
    directory: Path,
    pattern: re.Pattern[str],
    timestamp_format: str,
    *,
    effective_date: datetime | None = None,
    preserve_days: int = DEFAULT_PRESERVE_DAYS,
    first_bucket_days: int = DEFAULT_FIRST_BUCKET_DAYS,
    max_bucket_days: int = DEFAULT_MAX_BUCKET_DAYS,
    exponential_factor: int = DEFAULT_EXPONENTIAL_FACTOR,
) -> set[Path]:
    """Determine which files in directory should be deleted."""
    if effective_date is None:
        effective_date = datetime.now(UTC)

    files = list_matching_files(directory, pattern, timestamp_format)
    files_to_delete = _get_elements_to_delete(
        effective_date,
        set(files),
        lambda f: f.timestamp,
        timedelta(days=preserve_days),
        timedelta(days=first_bucket_days),
        timedelta(days=max_bucket_days),
        exponential_factor,
    )
    return {f.path for f in files_to_delete}


def delete_files(files: set[Path], *, dry_run: bool = False) -> None:
    """Delete the specified files."""
    for path in sorted(files):
        if dry_run:
            print(f"(dry run) would delete: {path}")
        else:
            print(f"deleting: {path}")
            path.unlink()


# =============================================================================
# CLI
# =============================================================================


def main(args: list[str] | None = None) -> int:
    parser = ArgumentParser(
        description='Prune old files using exponential bucket retention.',
        epilog='Example: %(prog)s -d ~/.local/share/tmux/resurrect '
        '-p "pane_contents\\.(?P<timestamp>\\d{4}-\\d{2}-\\d{2}T\\d{6})\\.tar\\.gz" '
        '-f "%%Y-%%m-%%dT%%H%%M%%S" --dry-run',
    )
    parser.add_argument(
        '--dir',
        '-d',
        type=Path,
        required=True,
        help='Directory to scan for files',
    )
    parser.add_argument(
        '--pattern',
        '-p',
        type=str,
        required=True,
        help='Regex pattern matching filenames, with named group "timestamp"',
    )
    parser.add_argument(
        '--format',
        '-f',
        type=str,
        required=True,
        help='strptime format string for parsing timestamps',
    )
    parser.add_argument(
        '--dry-run',
        '-n',
        action='store_true',
        help='Show what would be deleted without actually deleting',
    )
    parser.add_argument(
        '--preserve-days',
        type=int,
        default=DEFAULT_PRESERVE_DAYS,
        help=f'Days to preserve all files (default: {DEFAULT_PRESERVE_DAYS})',
    )
    parser.add_argument(
        '--first-bucket-days',
        type=int,
        default=DEFAULT_FIRST_BUCKET_DAYS,
        help=f'Size of first bucket in days (default: {DEFAULT_FIRST_BUCKET_DAYS})',
    )
    parser.add_argument(
        '--max-bucket-days',
        type=int,
        default=DEFAULT_MAX_BUCKET_DAYS,
        help=f'Maximum bucket size in days (default: {DEFAULT_MAX_BUCKET_DAYS})',
    )
    parser.add_argument(
        '--exponential-factor',
        type=int,
        default=DEFAULT_EXPONENTIAL_FACTOR,
        help=f'Bucket size growth factor (default: {DEFAULT_EXPONENTIAL_FACTOR})',
    )

    parsed = parser.parse_args(args)

    try:
        pattern = re.compile(parsed.pattern)
    except re.error as e:
        print(f"Invalid regex pattern: {e}", file=sys.stderr)
        return 1

    if 'timestamp' not in pattern.groupindex:
        print(
            'Pattern must contain a named group "timestamp", e.g. (?P<timestamp>...)',
            file=sys.stderr,
        )
        return 1

    try:
        files_to_delete = get_files_to_delete(
            parsed.dir,
            pattern,
            parsed.format,
            preserve_days=parsed.preserve_days,
            first_bucket_days=parsed.first_bucket_days,
            max_bucket_days=parsed.max_bucket_days,
            exponential_factor=parsed.exponential_factor,
        )
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if not files_to_delete:
        print("No files to delete.")
        return 0

    delete_files(files_to_delete, dry_run=parsed.dry_run)
    return 0


def cli_main() -> None:
    sys.exit(main())


if __name__ == '__main__':
    cli_main()
