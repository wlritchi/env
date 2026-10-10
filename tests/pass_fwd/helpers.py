"""Build synthetic OpenPGP packets and agent ciphertexts for tests."""

import os


def ecdh_parts() -> tuple[bytes, bytes]:
    """Return a random ephemeral point and wrapped key (with its length octet)."""
    ephemeral = b"\x40" + os.urandom(32)
    wrapped = b"\x30" + os.urandom(48)
    return ephemeral, wrapped


def pkesk_packet(ephemeral: bytes, wrapped: bytes) -> bytes:
    bits = (len(ephemeral) - 1) * 8 + ephemeral[0].bit_length()
    body = (
        b"\x03"
        + b"\x60\xf5\x53\x9d\x01\x36\x14\x13"
        + bytes([18])
        + bits.to_bytes(2, "big")
        + ephemeral
        + wrapped
    )
    return bytes([0x84, len(body)]) + body


def encrypted_file(*parts: tuple[bytes, bytes]) -> bytes:
    # A SEIPD packet with a partial body length follows the PKESKs.
    return b"".join(pkesk_packet(e, s) for e, s in parts) + b"\xd2\xe0" + os.urandom(64)


def agent_ciphertext(ephemeral: bytes, wrapped: bytes) -> bytes:
    return (
        b"(7:enc-val(4:ecdh(1:s%d:" % len(wrapped)
        + wrapped
        + b")(1:e%d:" % len(ephemeral)
        + ephemeral
        + b")))"
    )
