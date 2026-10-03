import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from wlrenv import reconcile_dotfiles as rd

ROOT = Path(__file__).resolve().parents[1]
SYNC = ROOT / 'bin/meta/wlr-sync-dotfiles'
UNAME = os.uname().sysname
HOST = 'testhost'


# ---------------------------------------------------------------------------
# Hunk logic


def lines(text: str) -> list[str]:
    return text.splitlines(keepends=True)


def test_apply_all_or_none_roundtrips() -> None:
    a = lines('a\nb\nc\nd\ne\nf\ng\nh\ni\nj\nk\nl\n')
    b = lines('a\nB\nc\nd\ne\nf\ng\nh\ni\nj\nK\nl\nm\n')
    hunks = rd.make_hunks(a, b)
    assert len(hunks) == 2
    assert rd.apply_hunks(a, b, hunks) == b
    assert rd.apply_hunks(a, b, []) == a
    assert rd.apply_hunks(a, b, hunks[:1]) == lines(
        'a\nB\nc\nd\ne\nf\ng\nh\ni\nj\nk\nl\n'
    )


def test_split_separates_nearby_changes() -> None:
    a = lines('a\nb\nc\nd\ne\n')
    b = lines('a\nB\nc\nD\ne\n')
    (hunk,) = rd.make_hunks(a, b)
    parts = hunk.split()
    assert len(parts) == 2
    assert rd.apply_hunks(a, b, parts) == b
    assert rd.apply_hunks(a, b, parts[1:]) == lines('a\nb\nc\nD\ne\n')
    # Context around each change aligns a and b.
    for part in parts:
        for tag, i1, i2, j1, j2 in part.ops:
            if tag == 'equal':
                assert a[i1:i2] == b[j1:j2]
    assert parts[0].split() == [parts[0]]


def test_hunk_text_marks_missing_final_newline() -> None:
    a = lines('a\nb\n')
    b = lines('a\nb')
    (hunk,) = rd.make_hunks(a, b)
    text = rd.hunk_text(hunk, a, b)
    assert text.startswith('@@ -1,2 +1,2 @@\n')
    assert '\\ No newline at end of file' in text
    assert rd.run_patch(b'a\nb\n', rd.unified(b'a\nb\n', b'a\nb', 'x')) == b'a\nb'


# ---------------------------------------------------------------------------
# End-to-end


@dataclass
class World:
    env: dict[str, str]
    public: Path
    home: Path
    private: Path | None

    def git(self, repo: Path, *args: str) -> str:
        result = subprocess.run(  # noqa: S603
            ['git', '-C', str(repo), *args],  # noqa: S607
            env=self.env,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout

    def sync(self) -> None:
        result = subprocess.run(  # noqa: S603
            ['bash', str(self.public / 'bin/meta/wlr-sync-dotfiles')],  # noqa: S607
            env=self.env,
            check=False,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    def reconcile(self, answers: str, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603
            [sys.executable, '-m', 'wlrenv.reconcile_dotfiles', '--interactive', *args],
            env=self.env,
            input=answers,
            capture_output=True,
            text=True,
            check=False,
        )

    def live(self, path: str) -> Path:
        return self.home / path

    def rendered(self, path: str, repo: Path | None = None) -> Path:
        return (repo or self.public) / 'rendered' / path

    def reference(self, path: str, repo: Path | None = None) -> Path:
        rendered = self.rendered(path, repo)
        return rendered.with_name(rendered.name + '.reference')

    def commits(self, repo: Path) -> list[str]:
        return self.git(repo, 'log', '--format=%s').splitlines()


def init_repo(world_env: dict[str, str], repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(  # noqa: S603
        ['git', 'init', '-q', '-b', 'main', str(repo)],  # noqa: S607
        env=world_env,
        check=True,
    )


def commit_all(world: World, repo: Path, message: str) -> None:
    world.git(repo, 'add', '--all')
    world.git(repo, 'commit', '-q', '-m', message)


@pytest.fixture
def world(tmp_path: Path, request: pytest.FixtureRequest) -> World:
    private_wanted = bool(getattr(request, 'param', False))
    home = tmp_path / 'home'
    home.mkdir()
    public = tmp_path / 'env'
    gitconfig = tmp_path / 'gitconfig'
    gitconfig.write_text(
        '[user]\n\tname = Test\n\temail = test@example.com\n[commit]\n\tgpgsign = false\n'
    )
    env = {
        'HOME': str(home),
        'WLR_ENV_PATH': str(public),
        'HOSTNAME': HOST,
        'PATH': f'{ROOT / "bin/early"}:/usr/bin:/bin',
        'GIT_CONFIG_NOSYSTEM': '1',
        'GIT_CONFIG_GLOBAL': str(gitconfig),
        'LC_ALL': 'C',
        'TERM': 'dumb',
        'PYTHONPATH': str(ROOT / 'src'),
    }
    init_repo(env, public)
    (public / 'bin/meta').mkdir(parents=True)
    shutil.copy(SYNC, public / 'bin/meta/wlr-sync-dotfiles')
    (public / '.gitignore').write_text('rendered\n')
    (public / 'dotfiles').mkdir()
    (public / 'dotfiles/.testrc').write_text('a\nb\nc\nd\ne\nf\ng\nh\ni\nj\nk\nl\n')
    (public / 'dotfiles/.plainrc').write_text('x\ny\n')
    (public / f'patches/host/{HOST}').mkdir(parents=True)
    (public / f'patches/host/{HOST}/.testrc.patch').write_bytes(
        rd.unified(b'a\nb\nc\n', b'a\nB\nc\n', 'dotfiles/.testrc')
    )
    private = None
    if private_wanted:
        private = home / '.wlrenv-private'
        init_repo(env, private)
        (private / '.gitignore').write_text('rendered\n')
        (private / 'dotfiles').mkdir()
    result = World(env, public, home, private)
    commit_all(result, public, 'initial')
    if private is not None:
        commit_all(result, private, 'initial')
    result.sync()
    return result


def test_no_drift_after_sync(world: World) -> None:
    result = world.reconcile('')
    assert result.returncode == 0, result.stderr
    assert 'no dotfile drift' in result.stdout


def test_check_reports_without_changing(world: World) -> None:
    rendered = world.rendered('.testrc')
    rendered.write_text(rendered.read_text() + 'm\n')
    result = world.reconcile('', '--check')
    assert result.returncode == 1
    assert '.testrc: 1 hunk(s)' in result.stdout
    assert rendered.read_text().endswith('l\nm\n')
    assert world.commits(world.public) == ['initial']


def test_accept_into_existing_host_patch(world: World) -> None:
    rendered = world.rendered('.testrc')
    rendered.write_text(rendered.read_text() + 'm\n')
    result = world.reconcile('a\n3\ny\n')
    assert result.returncode == 0, result.stderr
    patch = (world.public / f'patches/host/{HOST}/.testrc.patch').read_text()
    assert '+B\n' in patch and '+m\n' in patch
    assert rendered.read_text() == 'a\nB\nc\nd\ne\nf\ng\nh\ni\nj\nk\nl\nm\n'
    assert world.reference('.testrc').read_bytes() == rendered.read_bytes()
    assert os.path.realpath(world.live('.testrc')) == str(rendered)
    assert world.commits(world.public)[0] == f'dotfiles: reconcile drift on {HOST}'
    assert world.git(world.public, 'status', '--porcelain') == ''
    # The accepted content now renders cleanly from the repo alone.
    rendered.unlink()
    world.sync()
    assert rendered.read_text() == 'a\nB\nc\nd\ne\nf\ng\nh\ni\nj\nk\nl\nm\n'


def test_accept_globally_keeps_host_patch_applying(world: World) -> None:
    rendered = world.rendered('.testrc')
    rendered.write_text(rendered.read_text().replace('k\n', 'K\n'))
    result = world.reconcile('a\n1\ny\n')
    assert result.returncode == 0, result.stderr
    assert (
        world.public / 'dotfiles/.testrc'
    ).read_text() == 'a\nb\nc\nd\ne\nf\ng\nh\ni\nj\nK\nl\n'
    assert rendered.read_text() == 'a\nB\nc\nd\ne\nf\ng\nh\ni\nj\nK\nl\n'
    assert world.reference('.testrc').read_bytes() == rendered.read_bytes()
    assert 'dotfiles/.testrc' in world.git(world.public, 'show', '--stat', 'HEAD')


def test_detached_unpatched_file_into_new_uname_patch(world: World) -> None:
    live = world.live('.plainrc')
    live.unlink()
    live.write_text('x\ny\nz\n')
    result = world.reconcile('y\n2\ny\n')
    assert result.returncode == 0, result.stderr
    assert 'detached' in result.stdout
    patch = world.public / f'patches/uname/{UNAME}/.plainrc.patch'
    assert '+z\n' in patch.read_text()
    assert (world.public / 'dotfiles/.plainrc').read_text() == 'x\ny\n'
    rendered = world.rendered('.plainrc')
    assert rendered.read_text() == 'x\ny\nz\n'
    assert world.reference('.plainrc').read_text() == 'x\ny\nz\n'
    assert live.is_symlink() and os.path.realpath(live) == str(rendered)
    assert world.git(world.public, 'status', '--porcelain') == ''


def test_revert_restores_render(world: World) -> None:
    rendered = world.rendered('.testrc')
    original = rendered.read_bytes()
    rendered.write_bytes(original + b'm\n')
    result = world.reconcile('R\n')
    assert result.returncode == 0, result.stderr
    assert rendered.read_bytes() == original
    assert world.commits(world.public) == ['initial']


def test_skip_leaves_drift_in_place(world: World) -> None:
    rendered = world.rendered('.testrc')
    original = rendered.read_bytes()
    rendered.write_bytes(original + b'm\n')
    result = world.reconcile('d\n')
    assert result.returncode == 0, result.stderr
    assert rendered.read_bytes() == original + b'm\n'
    assert world.reference('.testrc').read_bytes() == original
    assert world.commits(world.public) == ['initial']


def test_quit_changes_nothing(world: World) -> None:
    rendered = world.rendered('.testrc')
    original = rendered.read_bytes()
    drifted = original.replace(b'a\n', b'A\n') + b'm\n'
    rendered.write_bytes(drifted)
    result = world.reconcile('q\n')
    assert result.returncode == 0, result.stderr
    assert rendered.read_bytes() == drifted
    assert world.commits(world.public) == ['initial']


def test_mixed_decisions_split_between_patch_and_live(world: World) -> None:
    rendered = world.rendered('.testrc')
    original = rendered.read_bytes()
    rendered.write_bytes(original.replace(b'a\n', b'A\n') + b'm\n')
    # Accept the first hunk, leave the second, do not revisit, commit.
    result = world.reconcile('y\nn\n3\nn\ny\n')
    assert result.returncode == 0, result.stderr
    patch = (world.public / f'patches/host/{HOST}/.testrc.patch').read_text()
    assert '+A\n' in patch and '+m\n' not in patch
    assert world.reference('.testrc').read_bytes() == original.replace(b'a\n', b'A\n')
    assert rendered.read_bytes() == original.replace(b'a\n', b'A\n') + b'm\n'


def test_cancelling_patch_removes_it(world: World) -> None:
    rendered = world.rendered('.testrc')
    rendered.write_bytes(rendered.read_bytes().replace(b'B\n', b'b\n'))
    result = world.reconcile('a\n3\ny\n')
    assert result.returncode == 0, result.stderr
    assert not (world.public / f'patches/host/{HOST}/.testrc.patch').exists()
    live = world.live('.testrc')
    assert os.path.realpath(live) == str(world.public / 'dotfiles/.testrc')
    assert not rendered.exists()
    assert world.git(world.public, 'status', '--porcelain') == ''


@pytest.mark.parametrize('world', [True], indirect=True)
def test_private_global_patch_on_public_base(world: World) -> None:
    assert world.private is not None
    rendered = world.rendered('.testrc')
    rendered.write_text(rendered.read_text() + 'm\n')
    # Destinations: 1-3 public global/uname/host, 4-6 private global/uname/host.
    result = world.reconcile('a\n4\ny\n')
    assert result.returncode == 0, result.stderr
    patch = world.private / 'patches/global/.testrc.patch'
    assert '+m\n' in patch.read_text()
    private_rendered = world.rendered('.testrc', world.private)
    assert private_rendered.read_text() == 'a\nB\nc\nd\ne\nf\ng\nh\ni\nj\nk\nl\nm\n'
    assert os.path.realpath(world.live('.testrc')) == str(private_rendered)
    assert world.commits(world.private)[0] == f'dotfiles: reconcile drift on {HOST}'
    assert world.commits(world.public) == ['initial']
    world.sync()
    assert private_rendered.read_text().endswith('l\nm\n')


def test_normalization_hides_formatting_only_drift(world: World) -> None:
    # A fake pre-commit runner that upper-cases the file stands in for a formatter.
    (world.public / '.pre-commit-config.yaml').write_text('repos: []\n')
    runner = world.public / '.venv/bin/prek'
    runner.parent.mkdir(parents=True)
    runner.write_text(
        '#!/bin/bash\nset -e\nf="${@: -1}"\n'
        'python3 -c "import sys,pathlib; p=pathlib.Path(sys.argv[1]); '
        'p.write_text(p.read_text().upper())" "$f"\nexit 1\n'
    )
    runner.chmod(0o755)
    base = world.public / 'dotfiles/.plainrc'
    base.write_text('X\nY\n')
    commit_all(world, world.public, 'uppercase')
    base_stat = base.stat()
    # The program rewrote the file in lower case and detached it from the symlink.
    live = world.live('.plainrc')
    live.unlink()
    live.write_text('x\ny\n')
    result = world.reconcile('')
    assert result.returncode == 0, result.stderr
    assert 'no dotfile drift' in result.stdout
    # The base file was borrowed for the hook run and put back untouched.
    assert base.read_text() == 'X\nY\n'
    assert base.stat().st_mtime_ns == base_stat.st_mtime_ns
    assert world.git(world.public, 'status', '--porcelain') == ''
    # Real drift still shows through the formatter.
    live.write_text('x\ny\nz\n')
    result = world.reconcile('', '--check')
    assert result.returncode == 1
    assert '.plainrc: 1 hunk(s) (detached)' in result.stdout
