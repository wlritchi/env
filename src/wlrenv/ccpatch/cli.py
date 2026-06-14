"""``ccpatch`` command line: reproducibly patch a Claude Code Bun binary.

Pipeline: read binary -> extract ``cli.js`` source from the Bun blob -> apply
length-free JS patches -> rebuild the blob (optionally zeroing bytecode) ->
repack the container -> verify (re-extract structurally + run ``--version``) ->
write output. Any failure exits non-zero so a Nix build fails loudly rather than
producing a binary that looks patched but isn't.
"""

from __future__ import annotations

import argparse
import os
import re
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path

from wlrenv.ccpatch.bunfmt import BunModule, parse_blob, rebuild_blob
from wlrenv.ccpatch.container import load_container
from wlrenv.ccpatch.patches import PatchError, default_patch_sets, parse_version

_CLAUDE_VERSION_RE = re.compile(r"\(Claude Code\)")


class ApplyError(RuntimeError):
    """The apply pipeline failed (patch, repack, or verification)."""


@dataclass(frozen=True)
class ApplyResult:
    out_path: Path
    version: str | None
    bytecode_zeroed: bool
    source_before: int
    source_after: int


def _decode(b: bytes) -> str:
    return b.decode("utf-8", "surrogateescape")


def _encode(s: str) -> bytes:
    return s.encode("utf-8", "surrogateescape")


def _patch_source(source: str, version: str | None) -> str:
    parsed = parse_version(version) if version else None
    for patch_set in default_patch_sets(parsed):
        if patch_set.applies_to(parsed):
            source = patch_set.apply(source)
    return source


def apply_patches(
    data: bytes,
    *,
    version: str | None,
    zero_bytecode: bool,
) -> bytes:
    """Return new binary bytes with patched ``cli.js`` source repacked in."""
    container = load_container(data)
    blob = parse_blob(container.read_blob())

    entry = next((m for m in blob.modules if m.is_entrypoint()), None)
    if entry is None:
        raise ApplyError("no entrypoint (cli.js) module found")

    patched_source = _patch_source(_decode(entry.contents), version)

    def transform(module: BunModule) -> BunModule | None:
        if not module.is_entrypoint():
            return None
        updates: dict[str, bytes] = {"contents": _encode(patched_source)}
        if zero_bytecode:
            updates["bytecode"] = b""
            updates["bytecode_origin_path"] = b""
        return replace(module, **updates)

    new_blob = rebuild_blob(blob.map_modules(transform))
    return container.write_blob(new_blob)


def _entry_source(data: bytes) -> str:
    blob = parse_blob(load_container(data).read_blob())
    entry = next(m for m in blob.modules if m.is_entrypoint())
    return _decode(entry.contents)


def _smoke_test(path: Path) -> str:
    """Run ``<binary> --version``; return the version line or raise."""
    try:
        result = subprocess.run(  # noqa: S603  # trusted constructed path
            [str(path), "--version"],
            capture_output=True,
            text=True,
            timeout=60,
            env={**os.environ, "DISABLE_AUTOUPDATER": "1"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ApplyError(f"patched binary failed to run: {exc}") from exc
    line = (result.stdout or "").splitlines()[0] if result.stdout else ""
    if result.returncode != 0 or not _CLAUDE_VERSION_RE.search(line):
        raise ApplyError(
            "smoke test failed: binary did not report a Claude version "
            f"(exit {result.returncode}, output {line!r}). Possible Bun fallback."
        )
    return line


def run_apply(
    in_path: Path,
    out_path: Path,
    *,
    version: str | None,
    zero_bytecode: bool,
    smoke: bool,
) -> ApplyResult:
    data = in_path.read_bytes()
    before = len(_entry_source(data))
    new_data = apply_patches(data, version=version, zero_bytecode=zero_bytecode)

    # Structural verify: the rewritten binary must re-extract and contain our edit.
    after_source = _entry_source(new_data)
    if "isTranscriptMode:true,verbose:true" not in after_source:
        raise ApplyError("structural verify failed: patched source not found in output")

    out_path.write_bytes(new_data)
    out_path.chmod(out_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    reported = _smoke_test(out_path) if smoke else None
    return ApplyResult(
        out_path=out_path,
        version=reported,
        bytecode_zeroed=zero_bytecode,
        source_before=before,
        source_after=len(after_source),
    )


def _cmd_apply(args: argparse.Namespace) -> int:
    out = Path(args.out) if args.out else Path(args.input)
    with tempfile.TemporaryDirectory() as tmp:
        staged = Path(tmp) / "claude"
        try:
            result = run_apply(
                Path(args.input),
                staged,
                version=args.version,
                zero_bytecode=args.zero_bytecode,
                smoke=not args.no_smoke,
            )
        except (ApplyError, PatchError) as exc:
            print(f"ccpatch: {exc}", file=sys.stderr)
            return 1
        out.write_bytes(staged.read_bytes())
        out.chmod(staged.stat().st_mode)
    delta = result.source_after - result.source_before
    print(
        f"ccpatch: patched {out} "
        f"(source {result.source_before} -> {result.source_after} bytes, {delta:+d}; "
        f"bytecode {'zeroed' if result.bytecode_zeroed else 'retained'}; "
        f"{result.version or 'no smoke test'})"
    )
    return 0


def _cmd_extract(args: argparse.Namespace) -> int:
    source = _entry_source(Path(args.input).read_bytes())
    Path(args.out).write_text(source, encoding="utf-8", errors="surrogateescape")
    print(f"ccpatch: wrote {len(source)} bytes of cli.js to {args.out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ccpatch", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    ap = sub.add_parser("apply", help="patch a Claude Code binary")
    ap.add_argument("input", help="path to the Bun standalone binary")
    ap.add_argument("-o", "--out", help="output path (default: overwrite input)")
    ap.add_argument("--version", help="Claude version, e.g. 2.1.170 (gates patches)")
    ap.add_argument(
        "--zero-bytecode",
        action="store_true",
        help="drop the entrypoint bytecode (forces recompile-from-source; shrinks binary)",
    )
    ap.add_argument(
        "--no-smoke", action="store_true", help="skip the --version smoke test"
    )
    ap.set_defaults(func=_cmd_apply)

    ex = sub.add_parser("extract", help="dump the embedded cli.js source")
    ex.add_argument("input", help="path to the Bun standalone binary")
    ex.add_argument("-o", "--out", required=True, help="output .js path")
    ex.set_defaults(func=_cmd_extract)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
