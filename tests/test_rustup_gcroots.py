import os
import shutil
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / 'bin/meta/wlr-rustup-gcroots'
MISSING_RUSTUP = '/nix/store/' + 'a' * 32 + '-rustup-0.0.0'
MISSING_LIB = '/nix/store/' + 'b' * 32 + '-zlib-0.0.0'
WRAPPER = 'lib/rustlib/x86_64-unknown-linux-gnu/bin/gcc-ld/ld.lld'


def nix_rustup() -> Path | None:
    found = shutil.which('rustup')
    if found is None:
        return None
    real = Path(found).resolve()
    if not str(real).startswith('/nix/store/') or real.parent.name != 'bin':
        return None
    return real.parent.parent


RUSTUP = nix_rustup()
pytestmark = pytest.mark.skipif(RUSTUP is None, reason='needs rustup from Nix')


@dataclass
class World:
    env: dict[str, str]
    toolchains: Path
    roots: Path
    rustup: Path

    def run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603
            ['bash', str(SCRIPT), *args],  # noqa: S607
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
        )

    def toolchain(self, name: str) -> Path:
        path = self.toolchains / name
        (path / WRAPPER).parent.mkdir(parents=True, exist_ok=True)
        (path / 'bin').mkdir(exist_ok=True)
        return path


@pytest.fixture
def world(tmp_path: Path) -> World:
    assert RUSTUP is not None
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    (bin_dir / 'rustup').symlink_to(RUSTUP / 'bin/rustup')
    # The stub makes the root link as nix-store would, without the daemon.
    stub = bin_dir / 'nix-store'
    stub.write_text('#!/bin/sh\nln -sfn "$4" "$2"\necho "$4"\n')
    stub.chmod(0o755)
    rg = shutil.which('rg')
    assert rg is not None
    (bin_dir / 'rg').symlink_to(rg)
    env = {
        'HOME': str(tmp_path / 'home'),
        'RUSTUP_HOME': str(tmp_path / 'rustup'),
        'XDG_STATE_HOME': str(tmp_path / 'state'),
        'PATH': f'{bin_dir}:{ROOT / "bin/early"}:/usr/bin:/bin',
        'TERM': 'dumb',
    }
    return World(
        env=env,
        toolchains=tmp_path / 'rustup/toolchains',
        roots=tmp_path / 'state/wlrenv/rustup-gcroots',
        rustup=RUSTUP,
    )


def write_wrapper(toolchain: Path, rustup: str) -> Path:
    wrapper = toolchain / WRAPPER
    wrapper.write_text(
        f'#!/usr/bin/env bash\n"{rustup}/nix-support/ld-wrapper.sh" "$@"\n'
    )
    wrapper.chmod(0o755)
    return wrapper


def write_binary(toolchain: Path, ref: str) -> None:
    (toolchain / 'bin/rustc').write_bytes(
        b'\x7fELF\x00\x01' + ref.encode() + b'/lib\x00\xff'
    )


def test_repairs_wrapper_and_roots_current_rustup(world: World) -> None:
    toolchain = world.toolchain('stable')
    wrapper = write_wrapper(toolchain, MISSING_RUSTUP)
    result = world.run()
    assert result.returncode == 0, result.stdout + result.stderr
    assert str(world.rustup) in wrapper.read_text()
    assert MISSING_RUSTUP not in wrapper.read_text()
    assert stat.S_IMODE(wrapper.stat().st_mode) == 0o755
    link = world.roots / world.rustup.name
    assert os.readlink(link) == str(world.rustup)


def test_roots_existing_refs_and_drops_stale_roots(world: World) -> None:
    toolchain = world.toolchain('stable')
    write_binary(toolchain, str(world.rustup))
    world.roots.mkdir(parents=True)
    stale = world.roots / 'stale'
    stale.symlink_to('/nix/store/' + 'c' * 32 + '-old')
    result = world.run()
    assert result.returncode == 0, result.stdout + result.stderr
    assert [p.name for p in world.roots.iterdir()] == [world.rustup.name]


def test_check_reports_without_changing(world: World) -> None:
    toolchain = world.toolchain('stable')
    wrapper = write_wrapper(toolchain, MISSING_RUSTUP)
    before = wrapper.read_text()
    result = world.run('--check')
    assert result.returncode == 1
    assert 'repairable' in result.stdout + result.stderr
    assert wrapper.read_text() == before
    assert not world.roots.exists()


def test_check_passes_when_rooted(world: World) -> None:
    toolchain = world.toolchain('stable')
    write_binary(toolchain, str(world.rustup))
    assert world.run().returncode == 0
    result = world.run('--check')
    assert result.returncode == 0, result.stdout + result.stderr


def test_missing_path_in_binary_needs_reinstall(world: World) -> None:
    toolchain = world.toolchain('nightly')
    write_binary(toolchain, MISSING_LIB)
    result = world.run()
    assert result.returncode == 1
    output = result.stdout + result.stderr
    assert f'refers to missing {MISSING_LIB}' in output
    assert 'rustup toolchain install nightly' in output


def test_no_toolchains_is_a_no_op(world: World) -> None:
    result = world.run()
    assert result.returncode == 0, result.stdout + result.stderr
    assert not world.roots.exists()
