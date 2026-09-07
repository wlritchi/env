import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'bin/meta/wlr-nix-rebuild'
REVISION = 'a' * 40


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('platform', ['Linux', 'Darwin'])
@pytest.mark.parametrize('changed', [False, True])
def test_committed_public_ref(
    tmp_path: Path, private: bool, platform: str, changed: bool
) -> None:
    public = tmp_path / 'public checkout'
    public.mkdir()
    overlay = tmp_path / '.wlrenv-private'
    if private:
        overlay.mkdir()
    log = tmp_path / 'calls'
    git_log = tmp_path / 'git-calls'
    startup = tmp_path / 'startup.bash'
    startup.write_text(
        '''git() {
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
'''
    )
    result = subprocess.run(  # noqa: S603
        ['bash', str(SCRIPT)],  # noqa: S607
        env={
            **os.environ,
            'HOME': str(tmp_path),
            'WLR_ENV_PATH': str(public),
            'BASH_ENV': str(startup),
            'TEST_LOG': str(log),
            'GIT_LOG': str(git_log),
            'SUDO_LOG': str(tmp_path / 'sudo'),
            'TEST_PLATFORM': platform,
            'TEST_CHANGED': str(changed).lower(),
            'TEST_REVISION': REVISION,
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert git_log.read_text().splitlines() == [
        '-C',
        str(public),
        'rev-parse',
        '--verify',
        'HEAD^{commit}',
    ]
    public_ref = f'git+file://{public}?rev={REVISION}'
    flake_ref = str(overlay) if private else public_ref
    directory = str(overlay if private else public)
    overrides = ['--override-input', 'wlrenv', public_ref] if private else []

    def expected_call(tool: str, operation: str) -> list[str]:
        return [
            directory,
            'run',
            '--impure',
            f'{public_ref}#{tool}',
            '--',
            operation,
            '--impure',
            '--flake',
            f'{flake_ref}#default',
            *overrides,
        ]

    calls = [call.splitlines() for call in log.read_text().split('END\n')[:-1]]
    expected = [expected_call('home-manager', 'switch')]
    if platform == 'Darwin':
        expected.append(expected_call('darwin-rebuild', 'build'))
        if changed:
            expected.append(expected_call('darwin-rebuild', 'switch'))
            assert (tmp_path / 'sudo').read_text().strip() == (
                '--preserve-env=NIX_HOSTNAME,NIX_SYSTEM,NIX_CONFIG,HOME'
            )
        assert not (Path(directory) / 'result').is_symlink()
    assert calls == expected
