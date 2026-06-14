"""Round-trip and edit tests for the Bun blob (de)serializer."""

from __future__ import annotations

import pytest

from wlrenv.ccpatch.bunfmt import (
    SIZEOF_MODULE_NEW,
    SIZEOF_MODULE_OLD,
    BunBlob,
    BunFormatError,
    BunModule,
    parse_blob,
    rebuild_blob,
)


def _module(
    name: bytes, contents: bytes, *, new: bool, bytecode: bytes = b""
) -> BunModule:
    return BunModule(
        name=name,
        contents=contents,
        sourcemap=b"",
        bytecode=bytecode,
        module_info=b"\x01\x02" if new else b"",
        bytecode_origin_path=name if new and bytecode else b"",
        tail=b"\x00\x01\x02\x03",
    )


def _blob(*, new: bool) -> BunBlob:
    size = SIZEOF_MODULE_NEW if new else SIZEOF_MODULE_OLD
    return BunBlob(
        entry_point_id=0,
        flags=0x2A,
        compile_exec_argv=b"--smol",
        modules=(
            _module(
                b"/$bunfs/root/cli.js", b"console.log(1)", new=new, bytecode=b"\xde\xad"
            ),
            _module(b"/$bunfs/root/helper.js", b"export const x=2", new=new),
        ),
        module_struct_size=size,
    )


@pytest.mark.parametrize("new", [True, False])
def test_round_trip_is_identity(new: bool) -> None:
    blob = _blob(new=new)
    assert parse_blob(rebuild_blob(blob)) == blob


@pytest.mark.parametrize("new", [True, False])
def test_rebuilt_blob_is_canonical_and_stable(new: bool) -> None:
    once = rebuild_blob(_blob(new=new))
    twice = rebuild_blob(parse_blob(once))
    assert once == twice  # rebuild is a fixed point


def test_entrypoint_detection() -> None:
    blob = _blob(new=True)
    entry = [m for m in blob.modules if m.is_entrypoint()]
    assert [m.name for m in entry] == [b"/$bunfs/root/cli.js"]


def test_swap_contents_changes_length_and_survives_round_trip() -> None:
    blob = _blob(new=True)

    def patch(m: BunModule) -> BunModule | None:
        if m.is_entrypoint():
            return BunModule(**{**m.__dict__, "contents": m.contents + b" /*patched*/"})
        return None

    patched = parse_blob(rebuild_blob(blob.map_modules(patch)))
    entry = next(m for m in patched.modules if m.is_entrypoint())
    assert entry.contents.endswith(b" /*patched*/")


def test_zeroing_bytecode_round_trips() -> None:
    blob = _blob(new=True)

    def drop_bytecode(m: BunModule) -> BunModule | None:
        return BunModule(**{**m.__dict__, "bytecode": b"", "bytecode_origin_path": b""})

    rebuilt = parse_blob(rebuild_blob(blob.map_modules(drop_bytecode)))
    assert all(m.bytecode == b"" for m in rebuilt.modules)


def test_missing_trailer_rejected() -> None:
    with pytest.raises(BunFormatError):
        parse_blob(b"not a bun blob")
