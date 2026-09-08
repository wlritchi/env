import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'bin/meta/wlr-check-update'


def run(command: list[str], env: dict[str, str]) -> str:
    result = subprocess.run(  # noqa: S603
        command, env=env, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('signature', ['trusted', 'untrusted', 'unsigned'])
def test_early_update_verifies_without_signing_wrapper(
    tmp_path: Path, private: bool, signature: str
) -> None:
    home = tmp_path / 'home'
    home.mkdir()
    startup = tmp_path / 'startup.bash'
    startup.write_text('curl() { return 0; }\n')
    config = home / '.gitconfig'
    config.write_text(
        '[user]\n\tname = Test User\n\temail = test@example.com\n'
        '[gpg]\n\tformat = ssh\n'
        '[gpg "ssh"]\n\tprogram = wlr-git-ssh-sign\n'
        '[commit]\n\tgpgsign = false\n'
    )
    env = {
        'HOME': str(home),
        'PATH': f'{ROOT / "bin/early"}:/usr/bin:/bin',
        'GIT_CONFIG_NOSYSTEM': '1',
        'GIT_CONFIG_GLOBAL': str(config),
        'GIT_TERMINAL_PROMPT': '0',
        'BASH_ENV': str(startup),
        'LC_ALL': 'C',
        'TERM': 'xterm',
    }
    trusted_key = tmp_path / 'trusted'
    untrusted_key = tmp_path / 'untrusted'
    for key in (trusted_key, untrusted_key):
        run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(key)], env)
    public = home / 'public checkout'
    env['WLR_ENV_PATH'] = str(public)
    checkouts = [public]
    if private:
        checkouts.append(home / '.wlrenv-private')
    target = checkouts[-1]
    original_commit = ''
    incoming_commit = ''
    for index, checkout in enumerate(checkouts):
        remote = tmp_path / f'remote-{index}'
        run(['git', 'init', '-b', 'main', str(remote)], env)
        (remote / '.allowed_signers').write_text(
            f'test@example.com {trusted_key.with_suffix(".pub").read_text()}'
        )
        run(['git', '-C', str(remote), 'add', '.allowed_signers'], env)
        run(['git', '-C', str(remote), 'commit', '-m', 'Initial signers'], env)
        run(['git', 'clone', str(remote), str(checkout)], env)
        if checkout == public:
            run(
                [
                    'git',
                    '-C',
                    str(checkout),
                    'remote',
                    'set-url',
                    'origin',
                    'https://github.com/wlritchi/env',
                ],
                env,
            )
            run(
                [
                    'git',
                    'config',
                    '--global',
                    f'url.{remote}.insteadOf',
                    'https://github.com/wlritchi/env',
                ],
                env,
            )
        if checkout != target:
            continue
        original_commit = run(['git', '-C', str(checkout), 'rev-parse', 'HEAD'], env)
        (remote / 'update').write_text('New content\n')
        run(['git', '-C', str(remote), 'add', 'update'], env)
        signing_args: list[str] = []
        if signature != 'unsigned':
            key = trusted_key if signature == 'trusted' else untrusted_key
            signing_args = [
                '-c',
                'gpg.ssh.program=ssh-keygen',
                '-c',
                f'user.signingkey={key}',
            ]
        run(
            [
                'git',
                '-C',
                str(remote),
                *signing_args,
                'commit',
                *(['-S'] if signing_args else []),
                '-m',
                'Update',
            ],
            env,
        )
        incoming_commit = run(['git', '-C', str(remote), 'rev-parse', 'HEAD'], env)

    assert run(['bash', '-c', 'command -v wlr-git-ssh-sign || true'], env) == ''
    assert run(['bash', '-c', 'command -v uv || true'], env) == ''
    original_config = config.read_text()
    result = subprocess.run(  # noqa: S603
        ['bash', str(SCRIPT)],  # noqa: S607
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    expected_commit = incoming_commit if signature == 'trusted' else original_commit
    assert run(['git', '-C', str(target), 'rev-parse', 'HEAD'], env) == expected_commit
    assert result.returncode == (0 if signature == 'trusted' else 1), result.stderr
    if signature != 'trusted':
        assert 'bad signature' in result.stderr + result.stdout
        assert not (target / 'update').exists()
    assert config.read_text() == original_config
