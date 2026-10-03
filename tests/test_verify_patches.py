"""End-to-end tests for bin/meta/wlr-verify-patches.

Each test builds a throwaway public repo (and optionally a private overlay)
with the two scripts copied in, then runs the verifier against it.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from wlrenv import reconcile_dotfiles as rd

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ('bin/meta/wlr-sync-dotfiles', 'bin/meta/wlr-verify-patches')

BASE = b'a\nb\nc\nd\ne\nf\ng\nh\ni\nj\nk\nl\n'
# Edits far enough apart that their hunks never overlap.
EDIT_TOP = BASE.replace(b'b\n', b'B\n')
EDIT_BOTTOM = BASE.replace(b'k\n', b'K\n')


@dataclass
class World:
    env: dict[str, str]
    public: Path
    private: Path | None

    def write(self, repo: Path, rel: str, content: bytes) -> Path:
        file = repo / rel
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(content)
        return file

    def patch(self, repo: Path, scope: str, path: str, old: bytes, new: bytes) -> Path:
        return self.write(
            repo,
            f'patches/{scope}/{path}.patch',
            rd.unified(old, new, f'dotfiles/{path}'),
        )

    def verify(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603
            ['bash', str(self.public / 'bin/meta/wlr-verify-patches')],  # noqa: S607
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
        )


@pytest.fixture
def world(tmp_path: Path, request: pytest.FixtureRequest) -> World:
    private_wanted = bool(getattr(request, 'param', False))
    home = tmp_path / 'home'
    home.mkdir()
    public = tmp_path / 'env'
    (public / 'bin/meta').mkdir(parents=True)
    for script in SCRIPTS:
        shutil.copy(ROOT / script, public / script)
    env = {
        'HOME': str(home),
        'WLR_ENV_PATH': str(public),
        'HOSTNAME': 'buildbox',
        'PATH': f'{ROOT / "bin/early"}:/usr/bin:/bin',
        'LC_ALL': 'C',
        'TERM': 'dumb',
    }
    private = None
    if private_wanted:
        private = home / '.wlrenv-private'
        (private / 'dotfiles').mkdir(parents=True)
        (private / 'patches').mkdir()
    result = World(env, public, private)
    result.write(public, 'dotfiles/.rc', BASE)
    return result


def test_no_patches_passes(world: World) -> None:
    result = world.verify()
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'All 0 patched file(s) rendered successfully' in result.stdout


def test_independent_layers_render_for_every_combination(world: World) -> None:
    world.patch(world.public, 'uname/Darwin', '.rc', BASE, EDIT_TOP)
    world.patch(world.public, 'host/mac1', '.rc', BASE, EDIT_BOTTOM)
    result = world.verify()
    assert result.returncode == 0, result.stdout + result.stderr
    for combo in (
        'host=mac1 uname=Darwin',
        'host=mac1 uname=Linux',
        'host=(none) uname=Darwin',
    ):
        assert f'[{combo}] 1 file(s) rendered' in result.stdout
    # No patch is active on a Linux machine without host-specific files.
    assert 'host=(none) uname=Linux' not in result.stdout


def test_host_and_uname_patches_that_conflict_fail(world: World) -> None:
    world.patch(world.public, 'uname/Darwin', '.rc', BASE, EDIT_TOP)
    host_patch = world.patch(world.public, 'host/mac1', '.rc', BASE, EDIT_TOP)
    result = world.verify()
    assert result.returncode == 1
    assert '[host=mac1 uname=Darwin] .rc' in result.stdout
    assert f'Failed to apply: {host_patch}' in result.stdout
    assert 'Applied first:' in result.stdout
    # The same host patch applies cleanly without the Darwin layer.
    assert '[host=mac1 uname=Linux] 1 file(s) rendered' in result.stdout


def test_pinned_uname_skips_impossible_combination(world: World) -> None:
    world.patch(world.public, 'uname/Darwin', '.rc', BASE, EDIT_TOP)
    world.patch(world.public, 'host/pc1', '.rc', BASE, EDIT_TOP)
    world.write(world.public, 'patches/host/pc1/.uname', b'Linux\n')
    result = world.verify()
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'host=pc1 uname=Darwin' not in result.stdout
    assert '[host=pc1 uname=Linux] 1 file(s) rendered' in result.stdout


def test_patch_without_base_file_fails(world: World) -> None:
    orphan = world.patch(world.public, 'global', '.missing', BASE, EDIT_TOP)
    result = world.verify()
    assert result.returncode == 1
    assert '.missing' in result.stdout
    assert 'No base file' in result.stdout
    assert str(orphan) in result.stdout


def test_uname_scoped_base_is_only_required_on_that_uname(world: World) -> None:
    world.write(world.public, 'dotfiles/uname/Darwin/.maconly', BASE)
    world.patch(world.public, 'uname/Darwin', '.maconly', BASE, EDIT_TOP)
    result = world.verify()
    assert result.returncode == 0, result.stdout + result.stderr
    assert '[host=(none) uname=Darwin] 1 file(s) rendered' in result.stdout


def test_stale_patch_against_current_base_fails(world: World) -> None:
    stale = world.patch(world.public, 'global', '.rc', b'x\ny\nz\n', b'x\nY\nz\n')
    result = world.verify()
    assert result.returncode == 1
    assert f'Failed to apply: {stale}' in result.stdout
    assert 'patch:' in result.stdout


@pytest.mark.parametrize('world', [True], indirect=True)
def test_private_patch_layers_after_public_patch(world: World) -> None:
    assert world.private is not None
    world.patch(world.public, 'host/pc1', '.rc', BASE, EDIT_TOP)
    world.patch(world.private, 'host/pc1', '.rc', EDIT_TOP, EDIT_TOP + b'm\n')
    result = world.verify()
    assert result.returncode == 0, result.stdout + result.stderr
    assert '[host=pc1 uname=Linux] 1 file(s) rendered' in result.stdout


@pytest.mark.parametrize('world', [True], indirect=True)
def test_private_patch_drift_fails(world: World) -> None:
    assert world.private is not None
    # The private patch was made against a base line that no longer exists.
    drifted = world.patch(world.private, 'global', '.rc', b'q\nr\ns\n', b'q\nR\ns\n')
    result = world.verify()
    assert result.returncode == 1
    assert f'Failed to apply: {drifted}' in result.stdout


@pytest.mark.parametrize('world', [True], indirect=True)
def test_private_host_only_known_to_overlay_is_checked(world: World) -> None:
    assert world.private is not None
    bad = world.patch(world.private, 'host/secret1', '.rc', b'q\nr\n', b'q\nR\n')
    result = world.verify()
    assert result.returncode == 1
    assert '[host=secret1 uname=' in result.stdout
    assert f'Failed to apply: {bad}' in result.stdout


@pytest.mark.parametrize('world', [True], indirect=True)
def test_public_patch_shadowed_by_private_base_is_inert(world: World) -> None:
    assert world.private is not None
    world.write(world.private, 'dotfiles/.rc', b'private\n')
    world.patch(world.public, 'global', '.rc', BASE, EDIT_TOP)
    result = world.verify()
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'Inert:' in result.stdout
    assert '2 inert public patch(es)' in result.stdout


@pytest.mark.parametrize('world', [True], indirect=True)
def test_private_patch_on_private_base(world: World) -> None:
    assert world.private is not None
    world.write(world.private, 'dotfiles/.rc', BASE)
    world.patch(world.private, 'uname/Linux', '.rc', BASE, EDIT_TOP)
    result = world.verify()
    assert result.returncode == 0, result.stdout + result.stderr
    assert '[host=(none) uname=Linux] 1 file(s) rendered' in result.stdout
