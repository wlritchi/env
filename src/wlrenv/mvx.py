"""mv with content verification, truncation detection, and symlink replacement.

Python port of the former bin/util/mvx bash script; the flag interface is
unchanged. See USAGE below for the full option reference.
"""

from __future__ import annotations

import getopt
import hashlib
import os
import shutil
import sys
from collections.abc import Iterator
from dataclasses import dataclass

USAGE = """\
Usage: {prog} [OPTIONS] SOURCE... DESTINATION

Move each SOURCE to DESTINATION.
If more than one SOURCE is provided, or if DESTINATION ends with /, then DESTINATION must be a directory into which each SOURCE will be moved.
Otherwise, if DESTINATION is not an existing directory, DESTINATION is treated as a target name for SOURCE.

OPTIONS:
    -p  Create parents of DESTINATION if they do not exist.

    -n  If a file already exists at DESTINATION, verify that its hash matches and remove SOURCE.
        In this mode, every SOURCE must be a file.

    -a  If a conflicting file exists at DESTINATION, search for alternate filenames instead of raising an error.
        Requires -n.

    -t  If a conflicting file exists at DESTINATION, check if one of the two files is a truncated copy of the other.
        If so, keep the longer file.
        If a file ends with at least 512 zero bytes, the trailing zeros are treated as an incomplete
        write: the file's effective size for this comparison is the position after its last nonzero
        byte, and the file with the greater effective size is kept.
        Requires -n.

    -l  Replace each SOURCE with a symlink to its new location.

    -R  Force the use of relative symlinks.
        Requires -l, incompatible with -A.

    -A  Force the use of absolute symlinks.
        Requires -l, incompatible with -R.

    -v  Print to stderr a record of the changes made. The output is not guaranteed to be stable; do not use it in scripts.

When creating symlinks, by default symlinks will be created as relative if they do not cross filesystem boundaries.
"""

ZERO_TAIL_PROBE = 512
CHUNK_SIZE = 1 << 20
ALT_LIMIT = 50


class MvxError(Exception):
    """A fatal per-file error; aborts the run with exit status 1."""


@dataclass
class Options:
    create_parents: bool = False
    verify_hash: bool = False
    alt_rename: bool = False
    check_truncated: bool = False
    link: bool = False
    relative: bool = False
    absolute: bool = False
    verbose: bool = False


def usage(prog: str) -> None:
    print(USAGE.format(prog=prog), file=sys.stderr)
    raise SystemExit(2)


def parse_args(argv: list[str], prog: str) -> tuple[Options, list[str], str]:
    try:
        optlist, positional = getopt.getopt(argv, 'pnatlRAv')
    except getopt.GetoptError:
        usage(prog)
    opts = Options()
    for flag, _ in optlist:
        match flag:
            case '-p':
                opts.create_parents = True
            case '-n':
                opts.verify_hash = True
            case '-a':
                opts.alt_rename = True
            case '-t':
                opts.check_truncated = True
            case '-l':
                opts.link = True
            case '-R':
                if opts.absolute:
                    usage(prog)
                opts.relative = True
            case '-A':
                if opts.relative:
                    usage(prog)
                opts.absolute = True
            case '-v':
                opts.verbose = True
    if len(positional) < 2:
        usage(prog)
    if (opts.alt_rename or opts.check_truncated) and not opts.verify_hash:
        usage(prog)
    if (opts.relative or opts.absolute) and not opts.link:
        usage(prog)
    return opts, positional[:-1], positional[-1]


def split_alt(path: str) -> tuple[str, str]:
    """Split into (stub, ext) for alternate-name generation.

    The extension is taken from the basename only, and leading-dot files are
    treated as extensionless.
    """
    return os.path.splitext(path)


def search_names(path: str, alt_rename: bool) -> Iterator[str]:
    """Candidate names an existing copy of the file might live under."""
    yield path
    if not alt_rename:
        return
    stub, ext = split_alt(path)
    yield f'{stub}.alt{ext}'
    if ext:
        yield f'{stub}{ext}.alt'
    for i in range(ALT_LIMIT + 1):
        yield f'{stub}.alt.{i}{ext}'
        yield f'{stub}.{i}.alt{ext}'
        if ext:
            yield f'{stub}{ext}.alt.{i}'
            yield f'{stub}{ext}.{i}.alt'


def move_names(path: str, alt_rename: bool) -> Iterator[str]:
    """Candidate names a new copy of the file may be created under."""
    yield path
    if not alt_rename:
        return
    stub, ext = split_alt(path)
    for i in range(ALT_LIMIT + 1):
        yield f'{stub}.alt.{i}{ext}'


def effective_size(path: str, size: int) -> int:
    """The file's size, ignoring a trailing run of zero bytes.

    If the final 512 bytes are all zero, the zero tail is assumed to be an
    incomplete write and the position after the last nonzero byte is returned
    instead of the size.
    """
    if size < ZERO_TAIL_PROBE:
        return size
    with open(path, 'rb') as f:
        f.seek(size - ZERO_TAIL_PROBE)
        if f.read(ZERO_TAIL_PROBE).rstrip(b'\0'):
            return size
        end = size
        while end > 0:
            start = max(0, end - CHUNK_SIZE)
            f.seek(start)
            stripped = len(f.read(end - start).rstrip(b'\0'))
            if stripped:
                return start + stripped
            end = start
    return 0


def hash_prefix(path: str, length: int | None = None) -> str:
    """sha256 of the first `length` bytes of the file (whole file if None)."""
    digest = hashlib.sha256()
    remaining = length
    with open(path, 'rb') as f:
        while remaining is None or remaining > 0:
            chunk = f.read(
                CHUNK_SIZE if remaining is None else min(CHUNK_SIZE, remaining)
            )
            if not chunk:
                break
            digest.update(chunk)
            if remaining is not None:
                remaining -= len(chunk)
    return digest.hexdigest()


def find_existing(src: str, dest: str, opts: Options) -> tuple[str, bool] | None:
    """Search candidate names for an existing copy of src.

    Returns (path, needs_move): needs_move is True when src supersedes the
    existing file (a -t "extend") and must be copied over it. Returns None if
    no candidate matches.
    """
    for candidate in search_names(dest, opts.alt_rename):
        if not os.path.isfile(candidate):
            continue
        if os.path.realpath(src) == os.path.realpath(candidate):
            return candidate, False
        src_size = os.path.getsize(src)
        candidate_size = os.path.getsize(candidate)
        if src_size == candidate_size and hash_prefix(src) == hash_prefix(candidate):
            return candidate, False
        if not opts.check_truncated:
            continue
        src_eff = effective_size(src, src_size)
        candidate_eff = effective_size(candidate, candidate_size)
        if src_eff < candidate_eff:
            if hash_prefix(src, src_eff) == hash_prefix(candidate, src_eff):
                print(f'# {src} -X(trunc)    {candidate}', file=sys.stderr)
                return candidate, False
        elif src_eff > candidate_eff:
            if hash_prefix(src, candidate_eff) == hash_prefix(candidate, candidate_eff):
                print(f'# {src} --(extend)--> {candidate}', file=sys.stderr)
                return candidate, True
        elif src_size != candidate_size:
            # equal effective sizes but different zero-padded tails
            if hash_prefix(src, src_eff) == hash_prefix(candidate, candidate_eff):
                if src_eff == src_size and candidate_eff != candidate_size:
                    # src is the clean copy; replace the zero-padded candidate
                    print(f'# {src} --(extend)--> {candidate}', file=sys.stderr)
                    return candidate, True
                print(f'# {src} -X(trunc)    {candidate}', file=sys.stderr)
                return candidate, False
    return None


def find_final(dest: str, opts: Options) -> str | None:
    """First candidate name with nothing already at it, or None."""
    for candidate in move_names(dest, opts.alt_rename):
        if not os.path.lexists(candidate):
            return candidate
    return None


def check_symlink_loop(src: str, dest: str) -> None:
    """Reject a dest whose symlink chain leads back to src."""
    src_abs = os.path.abspath(src)
    seen: set[str] = set()
    current = os.path.abspath(dest)
    while os.path.islink(current):
        target = os.readlink(current)
        if not os.path.isabs(target):
            target = os.path.join(os.path.dirname(current), target)
        current = os.path.normpath(target)
        if current == src_abs:
            raise MvxError(f'destination {dest} points back to source {src}')
        if current in seen:
            return  # self-contained symlink cycle; not a loop through src
        seen.add(current)


def copy_file(src: str, final: str) -> None:
    """Copy src to final, preserving metadata and symlink-ness (like rsync -a)."""
    if os.path.islink(src):
        if os.path.lexists(final):
            os.unlink(final)
        shutil.copy2(src, final, follow_symlinks=False)
    else:
        shutil.copy2(src, final)


def make_link(src: str, final: str, opts: Options) -> None:
    """Replace src with a symlink to final."""
    if opts.absolute:
        relative = False
    elif opts.relative:
        relative = True
    else:
        relative = os.stat(src).st_dev == os.stat(final).st_dev
    if relative:
        src_dir = os.path.dirname(os.path.abspath(src))
        target = os.path.relpath(os.path.abspath(final), src_dir)
    else:
        target = os.path.abspath(final)
    if os.path.lexists(src):
        os.unlink(src)
    os.symlink(target, src)


def move_to(src: str, dest: str, opts: Options) -> None:
    final = dest
    needs_move = True

    if opts.verify_hash:
        if not os.path.isfile(src):
            raise MvxError(f'{src} is not a file')
        check_symlink_loop(src, dest)
        existing = find_existing(src, dest, opts)
        if existing is not None:
            final, needs_move = existing
        else:
            found = find_final(dest, opts)
            if found is None:
                raise MvxError(
                    f'{src} does not match {dest}'
                    ' and no alternate filename is available'
                )
            final = found
    elif os.path.isdir(src) and not os.path.islink(src):
        raise MvxError(f'{src} is a directory; directory sources are not supported')

    if opts.verbose:
        if needs_move:
            arrow = 'mvln' if opts.link else 'mv'
            print(f'{src} --({arrow})--> {final}', file=sys.stderr)
        elif opts.link:
            print(f'{src} --(ln)--> {final}', file=sys.stderr)
        else:
            print(f'{src} -X(rm)    {final}', file=sys.stderr)

    if needs_move:
        if opts.create_parents:
            os.makedirs(os.path.dirname(os.path.abspath(final)), exist_ok=True)
        # keep src until after make_link, which needs it for the same-fs check
        copy_file(src, final)
    if opts.link:
        make_link(src, final, opts)
    elif os.path.abspath(src) == os.path.abspath(final):
        # src already sits at its destination entry; removing it would lose data
        print(
            f'Warning: {src} is already at its destination, skipping removal',
            file=sys.stderr,
        )
    else:
        os.unlink(src)


def main(argv: list[str] | None = None) -> None:
    prog = sys.argv[0]
    opts, sources, dest = parse_args(sys.argv[1:] if argv is None else argv, prog)

    dest_is_dir = not (
        len(sources) == 1 and not dest.endswith('/') and not os.path.isdir(dest)
    )
    if opts.create_parents:
        if dest_is_dir:
            os.makedirs(dest, exist_ok=True)
        else:
            parent = os.path.dirname(os.path.abspath(dest))
            os.makedirs(parent, exist_ok=True)

    for src in sources:
        if dest_is_dir:
            dest_dir = dest[:-1] if dest.endswith('/') else dest
            basename = os.path.basename(src.rstrip('/'))
            target = f'{dest_dir}/{basename}'
        else:
            target = dest
        try:
            move_to(src, target, opts)
        except MvxError as e:
            print(f'Error: {e}', file=sys.stderr)
            raise SystemExit(1) from None
        except OSError as e:
            print(f'Error: {e}', file=sys.stderr)
            raise SystemExit(1) from None


if __name__ == '__main__':
    main()
