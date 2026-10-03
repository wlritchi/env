"""Fold edits that programs made to live dotfiles back into the dotfile repos.

wlr-sync-dotfiles links each dotfile under $HOME to its source: the base
file in a repo when no patches apply, or rendered/$path when they do. The
render keeps a pristine copy at rendered/$path.reference. Programs that
rewrite their own config files create drift: the live file no longer
matches what was rendered, and a later re-render would discard the edits.

This tool compares each live dotfile with its expected content (the
reference for patched files, the committed base for unpatched ones). It
also catches live files that a program replaced with a regular file,
detaching them from the symlink. Before the comparison, it runs the repo's
pre-commit hooks on the live content so that tools which reformat their
config files do not show formatting noise as drift.

For each drifted file, the user walks through the hunks, much like
`git add -p`: revert a hunk, leave it in place, or accept it. Accepted
hunks go to the public repo or the private overlay, at global, uname, or
host scope. At global scope they edit the base file when it belongs to
that repo, and otherwise become a patches/global/ patch. At uname or host
scope they create or update the matching patch under patches/. The file is
then re-rendered, re-linked, and the repo changes are staged and offered
for commit.
"""

from __future__ import annotations

import argparse
import difflib
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

CONTEXT = 3
DIMS = ('global', 'uname', 'host')
Opcode = tuple[str, int, int, int, int]


class ReconcileError(Exception):
    """A per-file problem; the file is reported and skipped."""


class QuitError(Exception):
    """The user asked to stop, or stdin closed."""


def _color(code: str, text: str) -> str:
    if not sys.stdout.isatty():
        return text
    return f'\033[{code}m{text}\033[0m'


def say_good(message: str) -> None:
    print(f'{_color("32", "✔")} {message}')


def say_warn(message: str) -> None:
    print(f'{_color("33", "⚠")} {message}', file=sys.stderr)


def say_err(message: str) -> None:
    print(f'{_color("31", "🗙")} {message}', file=sys.stderr)


@dataclass(frozen=True)
class Repo:
    name: str
    root: Path

    def rel(self, file: Path) -> str:
        return os.path.relpath(file, self.root)


@dataclass(frozen=True)
class Env:
    home: Path
    public: Repo
    private: Repo | None
    hostname: str
    uname: str

    @property
    def repos(self) -> list[Repo]:
        return [self.public] + ([self.private] if self.private else [])

    @property
    def sync_script(self) -> Path:
        return self.public.root / 'bin' / 'meta' / 'wlr-sync-dotfiles'


@dataclass(frozen=True)
class Layer:
    repo: Repo
    dim: str
    value: str
    file: Path


@dataclass
class Resolution:
    path: str
    base_repo: Repo
    base: Path
    layers: list[Layer]
    rendered: Path | None

    @property
    def reference(self) -> Path | None:
        if self.rendered is None:
            return None
        return self.rendered.with_name(self.rendered.name + '.reference')


@dataclass(frozen=True)
class Destination:
    repo: Repo
    dim: str
    value: str
    kind: str  # 'base' edits the base file; 'patch' writes a patch file.
    file: Path

    @property
    def scope(self) -> str:
        return self.dim if self.dim == 'global' else f'{self.dim}/{self.value}'

    def describe(self) -> str:
        state = (
            'edit'
            if self.kind == 'base'
            else ('update' if self.file.exists() else 'new')
        )
        return f'{self.repo.name:<7} {self.scope:<16} {state:<6} {self.repo.rel(self.file)}'


@dataclass
class Hunk:
    ops: list[Opcode]

    @property
    def changes(self) -> list[Opcode]:
        return [op for op in self.ops if op[0] != 'equal']

    @property
    def start(self) -> int:
        return self.changes[0][1]

    def split(self) -> list[Hunk]:
        """Return one hunk per change, each with the context its group allows."""
        if len(self.changes) < 2:
            return [self]
        hunks: list[Hunk] = []
        for index, op in enumerate(self.ops):
            if op[0] == 'equal':
                continue
            ops: list[Opcode] = []
            if index > 0 and self.ops[index - 1][0] == 'equal':
                _, i1, i2, j1, j2 = self.ops[index - 1]
                k = min(CONTEXT, i2 - i1)
                ops.append(('equal', i2 - k, i2, j2 - k, j2))
            ops.append(op)
            if index + 1 < len(self.ops) and self.ops[index + 1][0] == 'equal':
                _, i1, i2, j1, j2 = self.ops[index + 1]
                k = min(CONTEXT, i2 - i1)
                ops.append(('equal', i1, i1 + k, j1, j1 + k))
            hunks.append(Hunk(ops))
        return hunks


def to_lines(data: bytes) -> list[str]:
    return data.decode('utf-8', 'surrogateescape').splitlines(keepends=True)


def to_bytes(lines: Sequence[str]) -> bytes:
    return ''.join(lines).encode('utf-8', 'surrogateescape')


def make_hunks(a: Sequence[str], b: Sequence[str]) -> list[Hunk]:
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    return [Hunk(list(group)) for group in matcher.get_grouped_opcodes(CONTEXT)]


def apply_hunks(a: Sequence[str], b: Sequence[str], hunks: Sequence[Hunk]) -> list[str]:
    out: list[str] = []
    pos = 0
    for hunk in sorted(hunks, key=lambda h: h.start):
        for _, i1, i2, j1, j2 in hunk.changes:
            out.extend(a[pos:i1])
            out.extend(b[j1:j2])
            pos = i2
    out.extend(a[pos:])
    return out


def hunk_text(hunk: Hunk, a: Sequence[str], b: Sequence[str]) -> str:
    i_start, j_start = hunk.ops[0][1], hunk.ops[0][3]
    i_end, j_end = hunk.ops[-1][2], hunk.ops[-1][4]
    lines = [
        f'@@ -{i_start + 1},{i_end - i_start} +{j_start + 1},{j_end - j_start} @@\n'
    ]

    def emit(prefix: str, source: Sequence[str], start: int, end: int) -> None:
        for line in source[start:end]:
            lines.append(prefix + line)
            if not line.endswith('\n'):
                lines.append('\n\\ No newline at end of file\n')

    for tag, i1, i2, j1, j2 in hunk.ops:
        if tag == 'equal':
            emit(' ', a, i1, i2)
        else:
            if tag in ('delete', 'replace'):
                emit('-', a, i1, i2)
            if tag in ('insert', 'replace'):
                emit('+', b, j1, j2)
    return ''.join(lines)


def run_patch(content: bytes, patch_text: bytes) -> bytes | None:
    """Apply a unified diff to content the way wlr-sync-dotfiles does."""
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp, 'source')
        source.write_bytes(content)
        patch_file = Path(tmp, 'patch')
        patch_file.write_bytes(patch_text)
        out = Path(tmp, 'out')
        result = subprocess.run(  # noqa: S603
            ['patch', str(source), str(patch_file), '-o', str(out)],  # noqa: S607
            capture_output=True,
            check=False,
        )
        if result.returncode != 0 or not out.exists():
            return None
        return out.read_bytes()


def unified(old: bytes, new: bytes, label: str) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        old_file = Path(tmp, 'old')
        old_file.write_bytes(old)
        new_file = Path(tmp, 'new')
        new_file.write_bytes(new)
        result = subprocess.run(  # noqa: S603
            [  # noqa: S607
                'diff',
                '-u',
                '--label',
                f'a/{label}',
                '--label',
                f'b/{label}',
                str(old_file),
                str(new_file),
            ],
            capture_output=True,
            check=False,
        )
        if result.returncode not in (0, 1):
            raise ReconcileError(
                f'diff failed: {result.stderr.decode(errors="replace")}'
            )
        return result.stdout


def render(base: bytes, layers: Sequence[Path]) -> list[bytes]:
    """Return the content after each layer; stages[0] is the base."""
    stages = [base]
    for layer in layers:
        stage = run_patch(stages[-1], layer.read_bytes())
        if stage is None:
            raise ReconcileError(f'patch no longer applies: {layer}')
        stages.append(stage)
    return stages


def detect_env() -> Env:
    env_path = os.environ.get('WLR_ENV_PATH')
    if not env_path:
        raise ReconcileError('WLR_ENV_PATH is not set')
    home = Path.home()
    private_root = home / '.wlrenv-private'
    private = Repo('private', private_root) if private_root.is_dir() else None
    # hostname and uname are filled in from the sync script's own view.
    return Env(home, Repo('public', Path(env_path)), private, '', '')


def resolve(env: Env, paths: Sequence[str]) -> tuple[Env, list[Resolution]]:
    sub_env = dict(os.environ)
    early = str(env.public.root / 'bin' / 'early')
    sub_env['PATH'] = early + os.pathsep + sub_env.get('PATH', '')
    result = subprocess.run(  # noqa: S603
        ['bash', str(env.sync_script), '--resolve', *paths],  # noqa: S607
        env=sub_env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise ReconcileError(f'wlr-sync-dotfiles --resolve failed:\n{result.stderr}')
    repos = {repo.name: repo for repo in env.repos}
    resolutions: list[Resolution] = []
    current: Resolution | None = None
    for line in result.stdout.splitlines():
        fields = line.split('\t')
        kind = fields[0]
        if kind == 'env':
            env = Env(env.home, env.public, env.private, fields[1], fields[2])
        elif kind == 'file':
            current = Resolution(fields[1], env.public, Path(), [], None)
            resolutions.append(current)
        elif current is None:
            raise ReconcileError(f'unexpected resolve record: {line}')
        elif kind == 'base':
            current.base_repo = repos[fields[1]]
            current.base = Path(fields[2])
        elif kind == 'layer':
            current.layers.append(
                Layer(repos[fields[1]], fields[2], fields[3], Path(fields[4]))
            )
        elif kind == 'rendered':
            current.rendered = Path(fields[1]) if fields[1] else None
    return env, resolutions


def candidates(env: Env, res: Resolution) -> list[Destination]:
    """All destinations for this file, in the order their layers apply."""
    out: list[Destination] = []
    base_real = Path(os.path.realpath(res.base))
    for repo in env.repos:
        # A private base shadows the public file, so public edits are inert.
        if repo.name == 'public' and res.base_repo.name == 'private':
            continue
        for dim in DIMS:
            value = {'global': '', 'uname': env.uname, 'host': env.hostname}[dim]
            if dim != 'global' and not value:
                continue
            if dim == 'global' and repo == res.base_repo:
                out.append(Destination(repo, dim, value, 'base', base_real))
                continue
            scope = dim if dim == 'global' else f'{dim}/{value}'
            file = repo.root / 'patches' / scope / f'{res.path}.patch'
            out.append(Destination(repo, dim, value, 'patch', file))
    existing = [d.file for d in out if d.kind == 'patch' and d.file.exists()]
    if existing != [layer.file for layer in res.layers]:
        raise ReconcileError(
            f'{res.path}: layer order differs between wlr-sync-dotfiles and this tool'
        )
    return out


def git(
    repo: Repo, *args: str, check: bool = True
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(  # noqa: S603
        ['git', '-C', str(repo.root), *args],  # noqa: S607
        capture_output=True,
        check=check,
    )


def committed_content(repo: Repo, file: Path) -> bytes | None:
    try:
        rel = file.relative_to(repo.root)
    except ValueError:
        return None
    result = git(repo, 'show', f'HEAD:{rel.as_posix()}', check=False)
    if result.returncode != 0:
        return None
    return result.stdout


def hook_runner(repo: Repo) -> list[str] | None:
    if not (repo.root / '.pre-commit-config.yaml').is_file():
        return None
    venv_prek = repo.root / '.venv' / 'bin' / 'prek'
    if venv_prek.is_file():
        return [str(venv_prek)]
    for name in ('prek', 'pre-commit'):
        found = shutil.which(name)
        if found:
            return [found]
    return None


def normalize(repo: Repo, target: Path, content: bytes, in_place: bool) -> bytes:
    """Run the repo's pre-commit hooks on content as if it were at target.

    The hooks only see files inside the repo, so the content is written to
    the tracked file at target for the duration of the run. When target is
    the live file itself (in_place), its content is simply reformatted.
    """
    runner = hook_runner(repo)
    if runner is None:
        return content
    try:
        rel = target.relative_to(repo.root)
    except ValueError:
        return content
    if (
        git(repo, 'ls-files', '--error-unmatch', '--', str(rel), check=False).returncode
        != 0
    ):
        return content
    saved = None if in_place else target.read_bytes()
    stat = target.stat()
    try:
        if not in_place:
            target.write_bytes(content)
        subprocess.run(  # noqa: S603
            [*runner, 'run', '--files', str(rel)],
            cwd=repo.root,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=300,
        )
        return target.read_bytes()
    finally:
        if saved is not None:
            target.write_bytes(saved)
            os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns))


@dataclass
class Drift:
    res: Resolution
    cands: list[Destination]
    live_path: Path
    linked: bool
    expected: bytes
    live: bytes
    base_content: bytes
    a: list[str] = field(init=False)
    b: list[str] = field(init=False)
    hunks: list[Hunk] = field(init=False)

    def __post_init__(self) -> None:
        self.a = to_lines(self.expected)
        self.b = to_lines(self.live)
        self.hunks = make_hunks(self.a, self.b)

    @property
    def patch_cands(self) -> list[Destination]:
        return [d for d in self.cands if d.kind == 'patch']

    @property
    def existing(self) -> list[Destination]:
        return [d for d in self.patch_cands if d.file.exists()]

    @property
    def label(self) -> str:
        return f'dotfiles/{self.res.path}'


def detect(env: Env, res: Resolution, do_normalize: bool) -> Drift | None:
    live_path = env.home / res.path
    if not live_path.is_file():
        return None
    base_real = Path(os.path.realpath(res.base))
    expected_target = res.rendered if res.rendered is not None else base_real
    linked = live_path.is_symlink() and Path(os.path.realpath(live_path)) == Path(
        os.path.realpath(expected_target)
    )
    live = live_path.read_bytes()
    if res.layers:
        base_content = base_real.read_bytes()
        reference = res.reference
        if reference is not None and reference.is_file():
            expected = reference.read_bytes()
        else:
            expected = render(base_content, [layer.file for layer in res.layers])[-1]
    else:
        committed = committed_content(res.base_repo, base_real)
        if committed is None:
            return None
        expected = base_content = committed
    if live == expected:
        return None
    if b'\0' in live or b'\0' in expected:
        say_warn(f'{res.path}: binary content differs; not handled here')
        return None
    cands = candidates(env, res)
    if do_normalize:
        in_place = linked and not res.layers
        live = normalize(res.base_repo, base_real, live, in_place)
        if live == expected:
            return None
    return Drift(res, cands, live_path, linked, expected, live, base_content)


def fold(drift: Drift, dest: Destination, hunks: Sequence[Hunk]) -> Path | None:
    """Write the hunks into dest; return the file to stage, or raise."""
    existing = drift.existing
    stages = render(drift.base_content, [d.file for d in existing])
    want = to_bytes(apply_hunks(drift.a, drift.b, hunks))
    delta = unified(drift.expected, want, drift.label)
    if dest.kind == 'base':
        new_base = run_patch(drift.base_content, delta)
        if new_base is None:
            raise ReconcileError('these hunks do not apply cleanly to the base file')
        render(new_base, [d.file for d in existing])
        dest.file.write_bytes(new_base)
        drift.base_content = new_base
        return dest.file
    position = drift.patch_cands.index(dest)
    before = [d for d in existing if drift.patch_cands.index(d) < position]
    after = [d for d in existing if drift.patch_cands.index(d) > position]
    previous = stages[len(before)]
    current = stages[len(before) + 1] if dest.file.exists() else previous
    new_stage = run_patch(current, delta)
    if new_stage is None:
        raise ReconcileError(f'these hunks do not apply cleanly at {dest.scope} scope')
    render(new_stage, [d.file for d in after])
    text = unified(previous, new_stage, drift.label)
    if text.strip():
        dest.file.parent.mkdir(parents=True, exist_ok=True)
        dest.file.write_bytes(text)
    elif dest.file.exists():
        # The accepted hunks cancel the whole patch.
        dest.file.unlink()
    else:
        return None
    return dest.file


def relink(live_path: Path, target: Path) -> None:
    if live_path.is_symlink() and Path(os.path.realpath(live_path)) == Path(
        os.path.realpath(target)
    ):
        return
    live_path.unlink()
    live_path.symlink_to(target)


def finalize(env: Env, drift: Drift, skipped: Sequence[Hunk]) -> None:
    """Re-render the file from the updated repos and leave skipped hunks live."""
    _, resolutions = resolve(env, [drift.res.path])
    res = resolutions[0]
    layers = [layer.file for layer in res.layers]
    rendered = render(drift.base_content, layers)[-1]
    live = rendered
    if skipped:
        want = to_bytes(apply_hunks(drift.a, drift.b, skipped))
        patched = run_patch(rendered, unified(drift.expected, want, drift.label))
        if patched is None:
            say_warn(
                f'{res.path}: the remaining hunks no longer fit the new render;'
                ' the live file is left as is and the next sync will show them'
            )
            live = drift.live
        else:
            live = patched
    base_real = Path(os.path.realpath(res.base))
    if res.rendered is not None and res.reference is not None:
        if base_real.read_bytes() != drift.base_content:
            base_real.write_bytes(drift.base_content)
        res.rendered.parent.mkdir(parents=True, exist_ok=True)
        res.rendered.write_bytes(live)
        res.reference.write_bytes(rendered)
        relink(drift.live_path, res.rendered)
    else:
        if base_real.read_bytes() != live:
            base_real.write_bytes(live)
        for repo in env.repos:
            stale = repo.root / 'rendered' / res.path
            for file in (stale, stale.with_name(stale.name + '.reference')):
                if file.is_file():
                    file.unlink()
        relink(drift.live_path, base_real)


# ---------------------------------------------------------------------------
# Interactive session


def ask(prompt: str, valid: str) -> str:
    while True:
        try:
            answer = input(prompt).strip()
        except EOFError as error:
            print()
            raise QuitError from error
        if len(answer) == 1 and answer in valid:
            return answer
        print(f'Please answer one of: {", ".join(valid)}')


def show_hunk(drift: Drift, hunk: Hunk) -> None:
    text = f'--- a/{drift.label}\n+++ b/{drift.label}\n' + hunk_text(
        hunk, drift.a, drift.b
    )
    delta = shutil.which('delta') if sys.stdout.isatty() else None
    if delta:
        result = subprocess.run(  # noqa: S603
            [delta, '--paging=never'], input=text.encode(), check=False
        )
        if result.returncode == 0:
            return
    for line in text.splitlines():
        if line.startswith('+'):
            print(_color('32', line))
        elif line.startswith('-'):
            print(_color('31', line))
        elif line.startswith('@@'):
            print(_color('36', line))
        else:
            print(line)


HUNK_HELP = """\
y - accept this hunk (choose where after this pass)
n - leave this hunk in the live file for now
r - revert this hunk to the rendered content
s - split this hunk into smaller hunks
a - accept this and all remaining hunks
d - leave this and all remaining hunks
R - revert this and all remaining hunks
q - stop here; files already handled stay handled
? - show this help"""


@dataclass
class Pass:
    accepted: list[Hunk] = field(default_factory=list)
    skipped: list[Hunk] = field(default_factory=list)
    reverted: list[Hunk] = field(default_factory=list)
    quit: bool = False


def choose_hunks(drift: Drift, hunks: list[Hunk]) -> Pass:
    """Walk the hunks once. On quit, every unhandled hunk counts as skipped."""
    result = Pass()
    queue = list(hunks)
    bulk = ''
    while queue:
        hunk = queue.pop(0)
        if bulk:
            {'a': result.accepted, 'd': result.skipped, 'R': result.reverted}[
                bulk
            ].append(hunk)
            continue
        print()
        show_hunk(drift, hunk)
        remaining = len(queue) + 1
        options = 'y,n,r,a,d,R,q,?' + (',s' if hunk.split() != [hunk] else '')
        while True:
            answer = ask(
                f'{_color("1;34", f"({remaining} left)")} Accept this hunk [{options}]? ',
                options.replace(',', ''),
            )
            if answer == '?':
                print(HUNK_HELP)
                continue
            break
        if answer == 'q':
            result.skipped.extend(result.accepted)
            result.accepted = []
            result.skipped.append(hunk)
            result.skipped.extend(queue)
            result.quit = True
            break
        if answer == 's':
            queue[0:0] = hunk.split()
            continue
        if answer in 'adR':
            bulk = answer
            {'a': result.accepted, 'd': result.skipped, 'R': result.reverted}[
                bulk
            ].append(hunk)
            continue
        {'y': result.accepted, 'n': result.skipped, 'r': result.reverted}[
            answer
        ].append(hunk)
    return result


def choose_destination(drift: Drift, count: int) -> Destination | None:
    print()
    print(f'Accept {count} hunk(s) into:')
    for index, dest in enumerate(drift.cands, start=1):
        print(f'  {index}) {dest.describe()}')
    print('  n) leave these hunks in the live file for now')
    valid = ''.join(str(i) for i in range(1, len(drift.cands) + 1)) + 'n'
    answer = ask('Destination? ', valid)
    if answer == 'n':
        return None
    return drift.cands[int(answer) - 1]


@dataclass
class Outcome:
    path: str
    notes: list[str] = field(default_factory=list)
    staged: list[tuple[Repo, Path]] = field(default_factory=list)


def reconcile(env: Env, drift: Drift) -> Outcome:
    res = drift.res
    outcome = Outcome(res.path)
    print()
    print(_color('1', f'━━━ {res.path}'))
    print(f'    base:   {res.base_repo.name} {res.base_repo.rel(res.base)}')
    if res.layers:
        print(
            '    layers: '
            + ', '.join(
                f'{layer.repo.name} {layer.dim}'
                + (f'/{layer.value}' if layer.value else '')
                for layer in res.layers
            )
        )
    state = (
        'symlink intact' if drift.linked else _color('33', 'detached from its symlink')
    )
    print(f'    live:   ~/{res.path} ({state})')
    print(f'    drift:  {len(drift.hunks)} hunk(s)')

    pending = list(drift.hunks)
    skipped_total: list[Hunk] = []
    quit_requested = False
    while pending:
        chosen = choose_hunks(drift, pending)
        pending = []
        skipped = chosen.skipped
        if chosen.accepted:
            dest = choose_destination(drift, len(chosen.accepted))
            if dest is None:
                skipped.extend(chosen.accepted)
            else:
                try:
                    staged = fold(drift, dest, chosen.accepted)
                except ReconcileError as error:
                    say_err(f'{res.path}: {error}; leaving those hunks in place')
                    skipped.extend(chosen.accepted)
                else:
                    outcome.notes.append(
                        f'{len(chosen.accepted)} hunk(s) -> {dest.repo.name} {dest.scope}'
                    )
                    if staged is not None:
                        outcome.staged.append((dest.repo, staged))
        if chosen.quit:
            quit_requested = True
        elif skipped and chosen.accepted:
            answer = ask(
                f'{len(skipped)} hunk(s) left; go through them again [y,n]? ', 'yn'
            )
            if answer == 'y':
                pending = skipped
                continue
        skipped_total.extend(skipped)
    finalize(env, drift, skipped_total)
    if skipped_total:
        outcome.notes.append(f'{len(skipped_total)} hunk(s) left in the live file')
    if quit_requested:
        raise QuitError
    return outcome


def stage_and_commit(env: Env, outcomes: Sequence[Outcome]) -> None:
    by_repo: dict[Repo, list[Path]] = {}
    for outcome in outcomes:
        for repo, file in outcome.staged:
            by_repo.setdefault(repo, []).append(file)
    for repo, files in by_repo.items():
        if git(repo, 'rev-parse', '--git-dir', check=False).returncode != 0:
            say_warn(
                f'{repo.root} is not a git repository; changes written but not staged'
            )
            continue
        rels = sorted({repo.rel(file) for file in files})
        git(repo, 'add', '--all', '--', *rels, check=False)
        print()
        print(_color('1', f'━━━ {repo.name} repo ({repo.root})'))
        status = git(repo, 'status', '--short', '--', *rels).stdout.decode(
            errors='replace'
        )
        print(status, end='')
        if not status.strip():
            continue
        if ask('Commit these changes [y,n]? ', 'yn') != 'y':
            say_warn(f'{repo.name}: changes left staged')
            continue
        lines = [f'dotfiles: reconcile drift on {env.hostname}', '']
        for outcome in outcomes:
            if any(r == repo for r, _ in outcome.staged):
                lines.append(f'- {outcome.path}: ' + '; '.join(outcome.notes))
        message = '\n'.join(lines) + '\n'
        result = git(repo, 'commit', '-m', message, '--', *rels, check=False)
        if result.returncode == 0:
            say_good(f'{repo.name}: committed')
        else:
            say_err(f'{repo.name}: commit failed; changes left staged')
            print(result.stderr.decode(errors='replace'), file=sys.stderr, end='')


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog='wlr-reconcile-dotfiles',
        description='Fold edits that programs made to live dotfiles back into the repos.',
    )
    parser.add_argument(
        'paths', nargs='*', help='dotfile paths relative to $HOME (default: all)'
    )
    parser.add_argument(
        '--check', action='store_true', help='only report drift; exit 1 if any'
    )
    parser.add_argument(
        '--interactive',
        action='store_true',
        help='prompt even when stdin is not a terminal (answers are read from stdin)',
    )
    parser.add_argument(
        '--no-normalize',
        action='store_true',
        help='do not run pre-commit hooks on live content before comparing',
    )
    args = parser.parse_args(argv)

    try:
        env, resolutions = resolve(detect_env(), args.paths)
    except ReconcileError as error:
        say_err(str(error))
        return 2

    drifts: list[Drift] = []
    for res in resolutions:
        try:
            drift = detect(env, res, not args.no_normalize)
        except ReconcileError as error:
            say_err(str(error))
            continue
        if drift is not None:
            drifts.append(drift)

    if not drifts:
        say_good('no dotfile drift')
        return 0

    interactive = args.interactive or (sys.stdin.isatty() and sys.stdout.isatty())
    if args.check or not interactive:
        for drift in drifts:
            state = '' if drift.linked else ' (detached)'
            print(f'{drift.res.path}: {len(drift.hunks)} hunk(s){state}')
        return 1 if args.check else 0

    outcomes: list[Outcome] = []
    for drift in drifts:
        try:
            outcomes.append(reconcile(env, drift))
        except QuitError:
            say_warn('stopped; remaining drift is left in place')
            break
        except ReconcileError as error:
            say_err(f'{drift.res.path}: {error}')
    try:
        stage_and_commit(env, outcomes)
    except QuitError:
        say_warn('stopped; changes are left staged')
    return 0


if __name__ == '__main__':
    sys.exit(main())
