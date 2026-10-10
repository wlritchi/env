"""Find the password store entry that a decryption request belongs to.

OpenPGP makes a new ephemeral ECDH key for each recipient of each encrypted file.
Thus the ephemeral point and the wrapped session key of a public-key encrypted
session key packet (PKESK) identify one file. gpg-agent receives the same two
values in the PKDECRYPT ciphertext, so the index can find the entry from the
request alone.
"""

import hashlib
import subprocess
import threading
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

PKESK_TAG = 1
MARKER_TAG = 10
ALGO_ECDH = 18
NULL_SHA = "0" * 40


class PacketError(ValueError):
    pass


def ecdh_key(ephemeral: bytes, wrapped: bytes) -> bytes:
    """Return the index key for an ephemeral point and a wrapped session key.

    `wrapped` includes its length octet, as in the PKESK and in the agent's
    S-expression.
    """
    return hashlib.sha256(
        len(ephemeral).to_bytes(2, "big") + ephemeral + wrapped
    ).digest()


def iter_packets(data: bytes) -> Iterator[tuple[int, bytes]]:
    """Yield (tag, body) for each packet up to the first one with a partial or
    indeterminate length. Only data packets use those lengths, and they come
    after the PKESKs."""
    pos = 0
    while pos < len(data):
        header = data[pos]
        if not header & 0x80:
            raise PacketError(f"no packet header at offset {pos}")
        pos += 1
        if header & 0x40:
            tag = header & 0x3F
            first = data[pos]
            if first < 192:
                length = first
                pos += 1
            elif first < 224:
                length = ((first - 192) << 8) + data[pos + 1] + 192
                pos += 2
            elif first == 255:
                length = int.from_bytes(data[pos + 1 : pos + 5], "big")
                pos += 5
            else:
                return
        else:
            tag = (header >> 2) & 0x0F
            length_type = header & 0x03
            if length_type == 3:
                return
            size = 1 << length_type
            length = int.from_bytes(data[pos : pos + size], "big")
            pos += size
        body = data[pos : pos + length]
        if len(body) < length:
            raise PacketError(f"truncated packet at offset {pos}")
        yield tag, body
        pos += length


def pkesk_key(body: bytes) -> bytes | None:
    """Return the index key of a v3 ECDH PKESK body, or None for other kinds."""
    if len(body) < 12 or body[0] != 3 or body[9] != ALGO_ECDH:
        return None
    bits = int.from_bytes(body[10:12], "big")
    end = 12 + (bits + 7) // 8
    ephemeral = body[12:end]
    wrapped = body[end:]
    if len(ephemeral) != end - 12 or not wrapped or wrapped[0] != len(wrapped) - 1:
        raise PacketError("malformed ECDH PKESK")
    return ecdh_key(ephemeral, wrapped)


def file_keys(data: bytes) -> list[bytes]:
    """Return the index keys of all ECDH PKESKs at the start of a file."""
    keys = []
    for tag, body in iter_packets(data):
        if tag == MARKER_TAG:
            continue
        if tag != PKESK_TAG:
            break
        if (key := pkesk_key(body)) is not None:
            keys.append(key)
    return keys


type Sexp = bytes | list[Sexp]


def parse_sexp(data: bytes) -> Sexp:
    """Parse a canonical S-expression, as gpg-agent uses."""

    def parse(pos: int) -> tuple[Sexp, int]:
        if data[pos : pos + 1] == b"(":
            items: list[Sexp] = []
            pos += 1
            while data[pos : pos + 1] != b")":
                if pos >= len(data):
                    raise PacketError("unterminated S-expression")
                item, pos = parse(pos)
                items.append(item)
            return items, pos + 1
        colon = data.index(b":", pos)
        digits = data[pos:colon]
        if not digits.isdigit():
            raise PacketError(f"bad atom length at offset {pos}")
        length = int(digits)
        start = colon + 1
        if start + length > len(data):
            raise PacketError("truncated S-expression atom")
        return data[start : start + length], start + length

    try:
        value, end = parse(0)
    except (IndexError, ValueError) as e:
        raise PacketError(f"malformed S-expression: {e}") from e
    if end != len(data):
        raise PacketError("trailing data after S-expression")
    return value


def ciphertext_key(ciphertext: bytes) -> bytes | None:
    """Return the index key of a PKDECRYPT ciphertext, or None if it is not ECDH.

    gpg-agent finds the values in the S-expression with its own search. A
    ciphertext with extra or repeated elements could make the two parsers read
    different values, so that the dialog names a different entry from the one
    that the agent decrypts. Thus only the exact form that gpg sends is accepted:
    `(enc-val (ecdh (s WRAPPED) (e EPHEMERAL)))`.
    """
    try:
        tree = parse_sexp(ciphertext)
    except PacketError:
        return None
    match tree:
        case [
            b"enc-val",
            [b"ecdh", [b"s", bytes() as wrapped], [b"e", bytes() as ephemeral]],
        ]:
            return ecdh_key(ephemeral, wrapped)
    return None


@dataclass(frozen=True)
class Lookup:
    """The entries that contain a ciphertext.

    `current` holds the paths in the working tree. `earlier` holds the other
    paths where the same ciphertext occurs in git history, for example the old
    name of a renamed entry.
    """

    current: tuple[str, ...] = ()
    earlier: tuple[str, ...] = ()

    @property
    def found(self) -> bool:
        return bool(self.current or self.earlier)


def entry_name(path: str) -> str:
    return path.removesuffix(".gpg")


class StoreIndex:
    def __init__(self, store: Path) -> None:
        self.store = store
        self._history_refs: str | None = None
        self._history: dict[bytes, set[str]] = {}
        self._lock = threading.Lock()

    def lookup(self, key: bytes) -> Lookup:
        with self._lock:
            current = sorted(self._current().get(key, set()))
            earlier = sorted(self._history_index().get(key, set()) - set(current))
        return Lookup(tuple(current), tuple(earlier))

    def _current(self) -> dict[bytes, set[str]]:
        # Read the working tree for each lookup, so that uncommitted entries and
        # recent pulls are included. The store is small enough for this.
        index: dict[bytes, set[str]] = defaultdict(set)
        for file in self.store.rglob("*.gpg"):
            rel = file.relative_to(self.store)
            if rel.parts[0] == ".git" or not file.is_file():
                continue
            try:
                keys = file_keys(file.read_bytes())
            except PacketError:
                continue
            for key in keys:
                index[key].add(entry_name(rel.as_posix()))
        return index

    def _git(self, *args: str, stdin: bytes | None = None) -> bytes:
        return subprocess.run(  # noqa: S603
            ["git", "-C", str(self.store), *args],  # noqa: S607
            input=stdin,
            capture_output=True,
            check=True,
        ).stdout

    def _history_index(self) -> dict[bytes, set[str]]:
        try:
            refs = self._git("for-each-ref", "--format=%(objectname) %(refname)")
            refs += self._git("rev-parse", "HEAD")
        except (subprocess.CalledProcessError, FileNotFoundError):
            return {}
        refs_text = refs.decode()
        if refs_text == self._history_refs:
            return self._history

        blob_paths = parse_raw_log(
            self._git(
                "log",
                "--all",
                "--no-renames",
                "--format=C%H",
                "--raw",
                "--no-abbrev",
                "-z",
            )
        )
        index: dict[bytes, set[str]] = defaultdict(set)
        for blob, content in read_blobs(
            self._git("cat-file", "--batch", stdin="\n".join(blob_paths).encode())
        ):
            try:
                keys = file_keys(content)
            except PacketError:
                continue
            for key in keys:
                index[key].update(entry_name(p) for p in blob_paths[blob])
        self._history = index
        self._history_refs = refs_text
        return index


def parse_raw_log(output: bytes) -> dict[str, set[str]]:
    """Map each added or changed `.gpg` blob in `git log --raw -z` output to its paths."""
    blob_paths: dict[str, set[str]] = defaultdict(set)
    tokens = output.decode(errors="surrogateescape").split("\0")
    i = 0
    while i < len(tokens):
        token = tokens[i].lstrip("\n")
        i += 1
        if not token.startswith(":"):
            continue
        fields = token[1:].split()
        path = tokens[i] if i < len(tokens) else ""
        i += 1
        if len(fields) < 5:
            continue
        new_blob, status = fields[3], fields[4]
        if status != "D" and new_blob != NULL_SHA and path.endswith(".gpg"):
            blob_paths[new_blob].add(path)
    return blob_paths


def read_blobs(output: bytes) -> Iterator[tuple[str, bytes]]:
    """Parse `git cat-file --batch` output into (sha, content) pairs."""
    pos = 0
    while pos < len(output):
        newline = output.index(b"\n", pos)
        header = output[pos:newline].split()
        pos = newline + 1
        if len(header) != 3:
            continue
        size = int(header[2])
        yield header[0].decode(), output[pos : pos + size]
        pos += size + 1
