import itertools
import os
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'bin/meta/wlr-pass-fwd-home'

pytestmark = pytest.mark.skipif(shutil.which('gpg') is None, reason='needs gpg')


def gpg(home: Path, *args: str) -> str:
    return subprocess.run(  # noqa: S603
        ['gpg', '--homedir', str(home), '--batch', *args],  # noqa: S607
        capture_output=True,
        check=True,
        text=True,
    ).stdout


def fingerprints(home: Path) -> set[str]:
    lines = gpg(home, '--with-colons', '--list-keys').splitlines()
    return {
        line.split(':')[9]
        for previous, line in itertools.pairwise(lines)
        if previous.startswith('pub:') and line.startswith('fpr:')
    }


@pytest.fixture(scope='module')
def source_keys() -> Iterator[tuple[Path, list[str]]]:
    # gpg-agent puts its socket in the home directory, so the path must be short.
    home = Path(tempfile.mkdtemp(prefix='pfh-', dir='/tmp'))
    home.chmod(0o700)
    try:
        for name in ('a', 'b'):
            gpg(
                home,
                '--passphrase',
                '',
                '--quick-gen-key',
                f'Test {name} <{name}@example.com>',
                'ed25519',
                'cert',
                'never',
            )
        yield home, sorted(fingerprints(home))
    finally:
        subprocess.run(  # noqa: S603
            ['gpgconf', '--homedir', str(home), '--kill', 'all'],  # noqa: S607
            check=False,
        )
        shutil.rmtree(home, ignore_errors=True)


def write_keys(private: Path, source: Path, fprs: list[str]) -> None:
    out = private / 'config/pass-fwd'
    out.mkdir(parents=True, exist_ok=True)
    (out / 'recipients.asc').write_text(gpg(source, '--armor', '--export', *fprs))
    (out / 'ownertrust.txt').write_text(
        '# test\n' + ''.join(f'{fpr}:6:\n' for fpr in fprs)
    )


def run_script(tmp_path: Path, private: Path) -> Path:
    home = tmp_path / 'fwd'
    env = {
        **os.environ,
        'HOME': str(tmp_path),
        'PRIVATE_ENV_PATH': str(private),
        'WLR_PASS_FWD_HOME': str(home),
    }
    subprocess.run([SCRIPT], env=env, check=True)  # noqa: S603
    return home


def test_imports_keys_and_trust(
    tmp_path: Path, source_keys: tuple[Path, list[str]]
) -> None:
    source, fprs = source_keys
    private = tmp_path / 'private'
    write_keys(private, source, fprs)
    home = run_script(tmp_path, private)
    assert fingerprints(home) == set(fprs)
    trust = gpg(home, '--export-ownertrust')
    assert all(f'{fpr}:6:' in trust for fpr in fprs)
    assert (home / 'gpg.conf').read_text() == 'no-autostart\n'
    assert home.stat().st_mode & 0o777 == 0o700


def test_second_run_removes_keys_not_in_file(
    tmp_path: Path, source_keys: tuple[Path, list[str]]
) -> None:
    source, fprs = source_keys
    private = tmp_path / 'private'
    write_keys(private, source, fprs)
    run_script(tmp_path, private)
    write_keys(private, source, fprs[:1])
    home = run_script(tmp_path, private)
    assert fingerprints(home) == {fprs[0]}
    assert (home / 'gpg.conf').read_text() == 'no-autostart\n'


def test_no_key_files_does_nothing(tmp_path: Path) -> None:
    home = run_script(tmp_path, tmp_path / 'missing')
    assert not home.exists()
