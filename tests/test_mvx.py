"""Tests for wlrenv.mvx, focused on the -n/-t content-comparison matrix."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from wlrenv import mvx

# deterministic, nonzero-tailed content
DATA = bytes(range(1, 256)) * 8


def run(args: list[str]) -> None:
    mvx.main(args)


def run_fail(args: list[str], code: int = 1) -> None:
    with pytest.raises(SystemExit) as excinfo:
        mvx.main(args)
    assert excinfo.value.code == code


@pytest.fixture(autouse=True)
def _in_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)


class TestBasicMoves:
    def test_move_to_directory(self, tmp_path: Path) -> None:
        (tmp_path / 'src').write_bytes(DATA)
        (tmp_path / 'd').mkdir()
        run(['src', 'd'])
        assert not (tmp_path / 'src').exists()
        assert (tmp_path / 'd' / 'src').read_bytes() == DATA

    def test_rename_to_target_name(self, tmp_path: Path) -> None:
        (tmp_path / 'src').write_bytes(DATA)
        run(['src', 'renamed'])
        assert (tmp_path / 'renamed').read_bytes() == DATA

    def test_trailing_slash_forces_directory(self, tmp_path: Path) -> None:
        (tmp_path / 'src').write_bytes(DATA)
        run(['-p', 'src', 'newdir/'])
        assert (tmp_path / 'newdir' / 'src').read_bytes() == DATA

    def test_multiple_sources(self, tmp_path: Path) -> None:
        (tmp_path / 'a').write_bytes(b'a')
        (tmp_path / 'b').write_bytes(b'b')
        (tmp_path / 'd').mkdir()
        run(['a', 'b', 'd'])
        assert (tmp_path / 'd' / 'a').read_bytes() == b'a'
        assert (tmp_path / 'd' / 'b').read_bytes() == b'b'

    def test_create_parents_of_target(self, tmp_path: Path) -> None:
        (tmp_path / 'src').write_bytes(DATA)
        run(['-p', 'src', 'x/y/renamed'])
        assert (tmp_path / 'x' / 'y' / 'renamed').read_bytes() == DATA

    def test_directory_source_rejected(self, tmp_path: Path) -> None:
        (tmp_path / 'srcdir').mkdir()
        (tmp_path / 'd').mkdir()
        run_fail(['srcdir', 'd'])
        assert (tmp_path / 'srcdir').is_dir()

    def test_usage_errors(self) -> None:
        run_fail(['only-one-arg'], code=2)
        run_fail(['-a', 'a', 'b'], code=2)  # -a requires -n
        run_fail(['-t', 'a', 'b'], code=2)  # -t requires -n
        run_fail(['-R', 'a', 'b'], code=2)  # -R requires -l
        run_fail(['-l', '-R', '-A', 'a', 'b'], code=2)  # -R and -A conflict


class TestVerifyHash:
    def test_identical_file_deduplicated(self, tmp_path: Path) -> None:
        (tmp_path / 'src').write_bytes(DATA)
        (tmp_path / 'dest').write_bytes(DATA)
        run(['-n', 'src', 'dest'])
        assert not (tmp_path / 'src').exists()
        assert (tmp_path / 'dest').read_bytes() == DATA

    def test_mismatch_without_alt_fails(self, tmp_path: Path) -> None:
        (tmp_path / 'src').write_bytes(DATA)
        (tmp_path / 'dest').write_bytes(b'different')
        run_fail(['-n', 'src', 'dest'])
        assert (tmp_path / 'src').read_bytes() == DATA
        assert (tmp_path / 'dest').read_bytes() == b'different'

    def test_mismatch_with_alt_moves_aside(self, tmp_path: Path) -> None:
        (tmp_path / 'src.txt').write_bytes(DATA)
        (tmp_path / 'dest.txt').write_bytes(b'different')
        run(['-n', '-a', 'src.txt', 'dest.txt'])
        assert not (tmp_path / 'src.txt').exists()
        assert (tmp_path / 'dest.txt').read_bytes() == b'different'
        assert (tmp_path / 'dest.alt.0.txt').read_bytes() == DATA

    def test_match_under_alt_name_deduplicated(self, tmp_path: Path) -> None:
        (tmp_path / 'src.txt').write_bytes(DATA)
        (tmp_path / 'dest.txt').write_bytes(b'different')
        (tmp_path / 'dest.alt.txt').write_bytes(DATA)
        run(['-n', '-a', 'src.txt', 'dest.txt'])
        assert not (tmp_path / 'src.txt').exists()
        assert (tmp_path / 'dest.alt.txt').read_bytes() == DATA

    def test_nonfile_source_rejected(self, tmp_path: Path) -> None:
        (tmp_path / 'srcdir').mkdir()
        run_fail(['-n', 'srcdir', 'dest'])

    def test_source_at_destination_survives(self, tmp_path: Path) -> None:
        (tmp_path / 'f').write_bytes(DATA)
        run(['-n', 'f', 'f'])
        assert (tmp_path / 'f').read_bytes() == DATA

    def test_dest_symlink_loop_rejected(self, tmp_path: Path) -> None:
        (tmp_path / 'src').write_bytes(DATA)
        (tmp_path / 'dest').symlink_to('src')
        run_fail(['-n', 'src', 'dest'])
        assert (tmp_path / 'src').read_bytes() == DATA


class TestBrokenSymlinks:
    def test_matching_broken_links_deduplicated(self, tmp_path: Path) -> None:
        (tmp_path / 'src').symlink_to('/nonexistent/target')
        (tmp_path / 'dest').symlink_to('/nonexistent/target')
        run(['-v', '-p', '-t', '-n', 'src', 'dest'])
        assert not (tmp_path / 'src').is_symlink()
        assert os.readlink(tmp_path / 'dest') == '/nonexistent/target'

    def test_matching_relative_broken_links_deduplicated(self, tmp_path: Path) -> None:
        (tmp_path / 'src').symlink_to('missing')
        (tmp_path / 'dest').symlink_to('missing')
        run(['-n', 'src', 'dest'])
        assert not (tmp_path / 'src').is_symlink()
        assert os.readlink(tmp_path / 'dest') == 'missing'

    def test_same_link_text_different_target_rejected(self, tmp_path: Path) -> None:
        (tmp_path / 'a').mkdir()
        (tmp_path / 'b').mkdir()
        (tmp_path / 'a' / 'src').symlink_to('../a/missing')
        (tmp_path / 'b' / 'dest').symlink_to('../b/missing')
        run_fail(['-n', 'a/src', 'b/dest'])
        assert (tmp_path / 'a' / 'src').is_symlink()
        assert (tmp_path / 'b' / 'dest').is_symlink()

    def test_broken_link_without_match_rejected(self, tmp_path: Path) -> None:
        (tmp_path / 'src').symlink_to('/nonexistent/target')
        (tmp_path / 'dest').write_bytes(DATA)
        run_fail(['-n', 'src', 'dest'])
        assert os.readlink(tmp_path / 'src') == '/nonexistent/target'
        assert (tmp_path / 'dest').read_bytes() == DATA

    def test_broken_link_onto_itself_survives(self, tmp_path: Path) -> None:
        (tmp_path / 'f').symlink_to('/nonexistent/target')
        run(['-n', 'f', 'f'])
        assert os.readlink(tmp_path / 'f') == '/nonexistent/target'

    def test_broken_link_onto_itself_with_l_survives(self, tmp_path: Path) -> None:
        (tmp_path / 'f').symlink_to('/nonexistent/target')
        run(['-n', '-l', 'f', 'f'])
        assert os.readlink(tmp_path / 'f') == '/nonexistent/target'

    def test_dest_resolving_through_broken_src_rejected(self, tmp_path: Path) -> None:
        (tmp_path / 'src').symlink_to('/nonexistent/target')
        (tmp_path / 'dest').symlink_to('src')
        run_fail(['-n', 'src', 'dest'])
        assert os.readlink(tmp_path / 'src') == '/nonexistent/target'
        assert os.readlink(tmp_path / 'dest') == 'src'

    def test_link_mode_relinks_src_to_dest(self, tmp_path: Path) -> None:
        (tmp_path / 'src').symlink_to('/nonexistent/target')
        (tmp_path / 'dest').symlink_to('/nonexistent/target')
        run(['-n', '-l', 'src', 'dest'])
        assert os.readlink(tmp_path / 'src') == 'dest'
        assert os.readlink(tmp_path / 'dest') == '/nonexistent/target'


class TestTruncationMatrix:
    def test_candidate_is_truncated_copy_extends(self, tmp_path: Path) -> None:
        (tmp_path / 'src').write_bytes(DATA)
        (tmp_path / 'dest').write_bytes(DATA[:1000])
        run(['-n', '-t', 'src', 'dest'])
        assert not (tmp_path / 'src').exists()
        assert (tmp_path / 'dest').read_bytes() == DATA

    def test_source_is_truncated_copy_dropped(self, tmp_path: Path) -> None:
        (tmp_path / 'src').write_bytes(DATA[:1000])
        (tmp_path / 'dest').write_bytes(DATA)
        run(['-n', '-t', 'src', 'dest'])
        assert not (tmp_path / 'src').exists()
        assert (tmp_path / 'dest').read_bytes() == DATA

    def test_clean_source_replaces_zero_padded_candidate(self, tmp_path: Path) -> None:
        (tmp_path / 'src').write_bytes(DATA)
        (tmp_path / 'dest').write_bytes(DATA + b'\0' * 600)
        run(['-n', '-t', 'src', 'dest'])
        assert not (tmp_path / 'src').exists()
        assert (tmp_path / 'dest').read_bytes() == DATA

    def test_zero_padded_source_dropped_for_clean_candidate(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / 'src').write_bytes(DATA + b'\0' * 600)
        (tmp_path / 'dest').write_bytes(DATA)
        run(['-n', '-t', 'src', 'dest'])
        assert not (tmp_path / 'src').exists()
        assert (tmp_path / 'dest').read_bytes() == DATA

    def test_both_zero_padded_keeps_candidate(self, tmp_path: Path) -> None:
        (tmp_path / 'src').write_bytes(DATA + b'\0' * 600)
        (tmp_path / 'dest').write_bytes(DATA + b'\0' * 1000)
        run(['-n', '-t', 'src', 'dest'])
        assert not (tmp_path / 'src').exists()
        assert (tmp_path / 'dest').read_bytes() == DATA + b'\0' * 1000

    def test_prefix_mismatch_is_not_truncation(self, tmp_path: Path) -> None:
        (tmp_path / 'src').write_bytes(DATA)
        (tmp_path / 'dest').write_bytes(b'x' + DATA[1:1000])
        run_fail(['-n', '-t', 'src', 'dest'])
        assert (tmp_path / 'src').read_bytes() == DATA

    def test_without_t_flag_truncated_copy_conflicts(self, tmp_path: Path) -> None:
        (tmp_path / 'src').write_bytes(DATA)
        (tmp_path / 'dest').write_bytes(DATA[:1000])
        run_fail(['-n', 'src', 'dest'])
        assert (tmp_path / 'src').read_bytes() == DATA
        assert (tmp_path / 'dest').read_bytes() == DATA[:1000]


class TestEffectiveSize:
    def size_of(self, path: Path) -> int:
        return mvx.effective_size(str(path), path.stat().st_size)

    def test_small_all_zero_file_keeps_size(self, tmp_path: Path) -> None:
        f = tmp_path / 'f'
        f.write_bytes(b'\0' * 100)
        assert self.size_of(f) == 100

    def test_all_zero_file_is_empty(self, tmp_path: Path) -> None:
        f = tmp_path / 'f'
        f.write_bytes(b'\0' * 512)
        assert self.size_of(f) == 0

    def test_nonzero_in_final_probe_keeps_size(self, tmp_path: Path) -> None:
        f = tmp_path / 'f'
        f.write_bytes(b'\0' * 1000 + b'x' + b'\0' * 100)
        assert self.size_of(f) == 1101

    def test_zero_tail_crossing_chunk_boundary(self, tmp_path: Path) -> None:
        f = tmp_path / 'f'
        f.write_bytes(b'\x01' + b'\0' * (2 * mvx.CHUNK_SIZE))
        assert self.size_of(f) == 1

    def test_no_zero_tail(self, tmp_path: Path) -> None:
        f = tmp_path / 'f'
        f.write_bytes(DATA)
        assert self.size_of(f) == len(DATA)


class TestSymlinkReplacement:
    def test_link_replaces_source(self, tmp_path: Path) -> None:
        (tmp_path / 'src').write_bytes(DATA)
        (tmp_path / 'd').mkdir()
        run(['-l', 'src', 'd'])
        link = tmp_path / 'src'
        assert link.is_symlink()
        assert link.read_bytes() == DATA
        # same filesystem, so the link defaults to relative
        assert not os.path.isabs(os.readlink(link))

    def test_absolute_link_forced(self, tmp_path: Path) -> None:
        (tmp_path / 'src').write_bytes(DATA)
        (tmp_path / 'd').mkdir()
        run(['-l', '-A', 'src', 'd'])
        assert os.path.isabs(os.readlink(tmp_path / 'src'))

    def test_dedup_leaves_link_to_existing(self, tmp_path: Path) -> None:
        (tmp_path / 'src').write_bytes(DATA)
        (tmp_path / 'dest').write_bytes(DATA)
        run(['-n', '-l', 'src', 'dest'])
        link = tmp_path / 'src'
        assert link.is_symlink()
        assert link.read_bytes() == DATA


class TestNameGeneration:
    def test_search_names_order(self) -> None:
        names = list(mvx.search_names('a/b.txt', True))
        assert names[:3] == ['a/b.txt', 'a/b.alt.txt', 'a/b.txt.alt']
        assert 'a/b.alt.0.txt' in names
        assert 'a/b.txt.0.alt' in names

    def test_extensionless_names(self) -> None:
        names = list(mvx.search_names('a/b', True))
        assert names[:2] == ['a/b', 'a/b.alt']
        assert 'a/b.alt.0' in names
        # extension-suffixed variants are skipped without an extension
        assert all('..' not in n for n in names)

    def test_dot_in_directory_does_not_split(self) -> None:
        assert list(mvx.move_names('a.d/b', False)) == ['a.d/b']
        assert next(iter(mvx.move_names('a.d/b', True))) == 'a.d/b'
        assert 'a.d/b.alt.0' in list(mvx.move_names('a.d/b', True))
