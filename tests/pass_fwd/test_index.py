import subprocess
from pathlib import Path

import pytest

from wlrenv.pass_fwd.index import (
    Lookup,
    PacketError,
    StoreIndex,
    ciphertext_key,
    ecdh_key,
    file_keys,
    parse_sexp,
)

from .helpers import (
    agent_ciphertext,
    ecdh_parts,
    encrypted_file,
    pkesk_packet,
)


def git(repo: Path, *args: str) -> None:
    subprocess.run(  # noqa: S603
        [  # noqa: S607
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        check=True,
        capture_output=True,
    )


class TestPackets:
    def test_file_keys_match_ciphertext_key(self) -> None:
        parts = ecdh_parts()
        assert file_keys(encrypted_file(parts)) == [
            ciphertext_key(agent_ciphertext(*parts))
        ]

    def test_file_with_two_recipients(self) -> None:
        a, b = ecdh_parts(), ecdh_parts()
        assert file_keys(encrypted_file(a, b)) == [ecdh_key(*a), ecdh_key(*b)]

    def test_new_format_header(self) -> None:
        parts = ecdh_parts()
        old = pkesk_packet(*parts)
        new = bytes([0xC1, old[1]]) + old[2:]
        assert file_keys(new) == [ecdh_key(*parts)]

    def test_non_ecdh_pkesk_is_skipped(self) -> None:
        body = b"\x03" + b"\x00" * 8 + bytes([1]) + b"\x00\x08\xff"
        assert file_keys(bytes([0x84, len(body)]) + body) == []

    def test_truncated_packet(self) -> None:
        with pytest.raises(PacketError):
            file_keys(pkesk_packet(*ecdh_parts())[:-5])

    def test_ciphertext_with_flags(self) -> None:
        e, s = ecdh_parts()
        ct = (
            b"(7:enc-val(5:flags)(4:ecdh(1:e%d:" % len(e)
            + e
            + b")(1:s%d:" % len(s)
            + s
            + b")))"
        )
        assert ciphertext_key(ct) == ecdh_key(e, s)

    def test_rsa_ciphertext_has_no_key(self) -> None:
        assert ciphertext_key(b"(7:enc-val(3:rsa(1:a3:abc)))") is None

    def test_garbage_ciphertext_has_no_key(self) -> None:
        assert ciphertext_key(b"(7:enc-val(4:ecdh") is None

    def test_parse_sexp_rejects_trailing_data(self) -> None:
        with pytest.raises(PacketError):
            parse_sexp(b"(1:a)x")


class TestStoreIndex:
    @pytest.fixture
    def store(self, tmp_path: Path) -> Path:
        store = tmp_path / "store"
        store.mkdir()
        git(store, "init", "-q")
        return store

    def test_current_entry(self, store: Path) -> None:
        parts = ecdh_parts()
        (store / "Social").mkdir()
        (store / "Social/foo.gpg").write_bytes(encrypted_file(parts))
        assert StoreIndex(store).lookup(ecdh_key(*parts)) == Lookup(
            current=("Social/foo",)
        )

    def test_unknown_ciphertext(self, store: Path) -> None:
        assert not StoreIndex(store).lookup(ecdh_key(*ecdh_parts())).found

    def test_renamed_entry_lists_earlier_name(self, store: Path) -> None:
        parts = ecdh_parts()
        (store / "old.gpg").write_bytes(encrypted_file(parts))
        git(store, "add", "-A")
        git(store, "commit", "-qm", "add")
        git(store, "mv", "old.gpg", "new.gpg")
        git(store, "commit", "-qm", "rename")
        assert StoreIndex(store).lookup(ecdh_key(*parts)) == Lookup(
            current=("new",), earlier=("old",)
        )

    def test_old_version_only_in_history(self, store: Path) -> None:
        parts = ecdh_parts()
        (store / "bank.gpg").write_bytes(encrypted_file(parts))
        git(store, "add", "-A")
        git(store, "commit", "-qm", "add")
        (store / "bank.gpg").write_bytes(encrypted_file(ecdh_parts()))
        git(store, "commit", "-qam", "change")
        assert StoreIndex(store).lookup(ecdh_key(*parts)) == Lookup(earlier=("bank",))

    def test_copied_ciphertext_shows_both_paths(self, store: Path) -> None:
        parts = ecdh_parts()
        data = encrypted_file(parts)
        (store / "bank.gpg").write_bytes(data)
        (store / "harmless.gpg").write_bytes(data)
        assert StoreIndex(store).lookup(ecdh_key(*parts)).current == (
            "bank",
            "harmless",
        )

    def test_history_updates_after_new_commit(self, store: Path) -> None:
        index = StoreIndex(store)
        first = ecdh_parts()
        (store / "a.gpg").write_bytes(encrypted_file(first))
        git(store, "add", "-A")
        git(store, "commit", "-qm", "a")
        assert index.lookup(ecdh_key(*first)).current == ("a",)
        git(store, "mv", "a.gpg", "b.gpg")
        git(store, "commit", "-qm", "b")
        assert index.lookup(ecdh_key(*first)) == Lookup(current=("b",), earlier=("a",))
