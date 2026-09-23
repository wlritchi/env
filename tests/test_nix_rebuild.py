import os
import subprocess
from pathlib import Path

import pytest

BIN = Path(__file__).resolve().parents[1] / 'bin/meta'
REBUILD = BIN / 'wlr-nix-rebuild'
HM = BIN / 'wlr-nix-hm'
REVISION = 'a' * 40

STARTUP = '''git() {
    printf '%s\\n' "$@" >> "$GIT_LOG"
    printf '%s\\n' "$TEST_REVISION"
}
nix() {
    printf '%s\\n' "$PWD" "$@" END >> "$TEST_LOG"
    if [[ "$*" == *' -- build '* ]]; then
        ln -s /new-system result
    fi
}
uname() { printf '%s\\n' "$TEST_PLATFORM"; }
hostname() { printf '%s\\n' test-host; }
readlink() {
    if [[ "$1" == /run/current-system && "$TEST_CHANGED" == true ]]; then
        printf '%s\\n' /old-system
    else
        printf '%s\\n' /new-system
    fi
}
sudo() {
    printf '%s\\n' "$1" > "$SUDO_LOG"
    shift
    "$@"
}
wlr-warn() { printf '%s\\n' "$*" >> "$WARN_LOG"; }
wlr-err() { printf '%s\\n' "$*" >> "$WARN_LOG"; }
'''


class Harness:
    def __init__(self, tmp_path: Path, private: bool) -> None:
        self.tmp_path = tmp_path
        self.public = tmp_path / 'public checkout'
        self.public.mkdir()
        self.overlay = tmp_path / '.wlrenv-private'
        if private:
            self.overlay.mkdir()
        self.log = tmp_path / 'calls'
        self.git_log = tmp_path / 'git-calls'
        self.warn_log = tmp_path / 'warnings'
        self.sudo_log = tmp_path / 'sudo'
        startup = tmp_path / 'startup.bash'
        startup.write_text(STARTUP)
        self.env = {
            **os.environ,
            'HOME': str(tmp_path),
            'XDG_CONFIG_HOME': str(tmp_path / '.config'),
            'WLR_ENV_PATH': str(self.public),
            'BASH_ENV': str(startup),
            'TEST_LOG': str(self.log),
            'GIT_LOG': str(self.git_log),
            'WARN_LOG': str(self.warn_log),
            'SUDO_LOG': str(self.sudo_log),
            'TEST_PLATFORM': 'Linux',
            'TEST_CHANGED': 'false',
            'TEST_REVISION': REVISION,
        }
        self.public_ref = f'git+file://{self.public}?rev={REVISION}'
        self.private = private
        self.flake_ref = str(self.overlay) if private else self.public_ref
        self.directory = str(self.overlay if private else self.public)
        self.overrides = (
            ['--override-input', 'wlrenv', self.public_ref] if private else []
        )

    def run(self, script: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603
            ['bash', str(script), *args],  # noqa: S607
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
        )

    def expected_call(
        self, tool: str, operation: list[str], extra_overrides: list[str] | None = None
    ) -> list[str]:
        return [
            self.directory,
            'run',
            '--impure',
            f'{self.public_ref}#{tool}',
            '--',
            *operation,
            '--impure',
            '--flake',
            f'{self.flake_ref}#default',
            *self.overrides,
            *(extra_overrides or []),
        ]

    def calls(self) -> list[list[str]]:
        return [call.splitlines() for call in self.log.read_text().split('END\n')[:-1]]

    def input_override(self, name: str, ref: str) -> list[str]:
        prefix = 'wlrenv/' if self.private else ''
        return ['--override-input', f'{prefix}{name}', ref]


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('platform', ['Linux', 'Darwin'])
@pytest.mark.parametrize('changed', [False, True])
def test_committed_public_ref(
    tmp_path: Path, private: bool, platform: str, changed: bool
) -> None:
    harness = Harness(tmp_path, private)
    harness.env['TEST_PLATFORM'] = platform
    harness.env['TEST_CHANGED'] = str(changed).lower()
    result = harness.run(REBUILD)
    assert result.returncode == 0, result.stderr
    assert harness.git_log.read_text().splitlines() == [
        '-C',
        str(harness.public),
        'rev-parse',
        '--verify',
        'HEAD^{commit}',
    ]
    expected = [harness.expected_call('home-manager', ['switch'])]
    if platform == 'Darwin':
        expected.append(harness.expected_call('darwin-rebuild', ['build']))
        if changed:
            expected.append(harness.expected_call('darwin-rebuild', ['switch']))
            assert harness.sudo_log.read_text().strip() == (
                '--preserve-env=NIX_HOSTNAME,NIX_SYSTEM,NIX_CONFIG,HOME'
            )
        assert not (Path(harness.directory) / 'result').is_symlink()
    assert harness.calls() == expected


@pytest.mark.parametrize('private', [False, True])
def test_hm_forwards_subcommand(tmp_path: Path, private: bool) -> None:
    harness = Harness(tmp_path, private)
    result = harness.run(HM, 'news')
    assert result.returncode == 0, result.stderr
    assert harness.calls() == [harness.expected_call('home-manager', ['news'])]


@pytest.mark.parametrize('private', [False, True])
def test_hm_input_overrides(tmp_path: Path, private: bool) -> None:
    harness = Harness(tmp_path, private)
    config = tmp_path / '.config' / 'wlrenv'
    config.mkdir(parents=True)
    (config / 'nix-input-overrides').write_text(
        '# comment\n\n  ccpatch=~/ccpatch  # trailing\n'
    )
    result = harness.run(
        HM, '--override-input', 'other', 'path:/x', 'build', '--dry-run'
    )
    assert result.returncode == 0, result.stderr
    assert harness.calls() == [
        harness.expected_call(
            'home-manager',
            ['build', '--dry-run'],
            [
                *harness.input_override('ccpatch', f'{tmp_path}/ccpatch'),
                *harness.input_override('other', 'path:/x'),
            ],
        )
    ]
    assert harness.warn_log.read_text().splitlines() == [
        f'overriding flake input ccpatch with {tmp_path}/ccpatch',
        'overriding flake input other with path:/x',
    ]


def test_hm_requires_subcommand(tmp_path: Path) -> None:
    harness = Harness(tmp_path, private=False)
    result = harness.run(HM)
    assert result.returncode == 1
    assert 'usage: wlr-nix-hm' in result.stderr
    assert not harness.log.exists()


def test_rebuild_rejects_positional(tmp_path: Path) -> None:
    harness = Harness(tmp_path, private=False)
    result = harness.run(REBUILD, 'news')
    assert result.returncode == 1
    assert 'usage: wlr-nix-rebuild' in result.stderr
    assert not harness.log.exists()
