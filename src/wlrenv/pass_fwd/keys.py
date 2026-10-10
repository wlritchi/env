"""Find the keygrips of the smartcard keys that remote machines may use."""

import subprocess
from dataclasses import dataclass, field

# A token serial field of "#" means that the key is not available, and "+" means
# that the key is on disk. A card key has the card serial number.
NOT_ON_CARD = {"", "#", "+"}
UNUSABLE_VALIDITY = {"e", "r", "d", "i"}


@dataclass(frozen=True)
class CardKey:
    key_id: str
    keygrip: str


@dataclass(frozen=True)
class KeyPolicy:
    """Map keygrips to the card keys that may decrypt or sign."""

    decrypt: dict[str, CardKey] = field(default_factory=dict)
    sign: dict[str, CardKey] = field(default_factory=dict)


def parse_card_keys(colons: str) -> KeyPolicy:
    """Parse `gpg -K --with-keygrip --with-colons` output.

    Only keys on a smartcard are included. A remote machine must not use the
    keys on disk, because they do not need a touch.
    """
    decrypt: dict[str, CardKey] = {}
    sign: dict[str, CardKey] = {}
    record: list[str] | None = None
    for line in colons.splitlines():
        fields = line.split(":")
        if fields[0] in ("sec", "ssb"):
            record = fields
            continue
        if fields[0] != "grp" or record is None:
            continue
        keygrip = fields[9].upper()
        caps = record[11] if len(record) > 11 else ""
        serial = record[14] if len(record) > 14 else ""
        usable = record[1] not in UNUSABLE_VALIDITY and serial not in NOT_ON_CARD
        key = CardKey(key_id=record[4].upper(), keygrip=keygrip)
        record = None
        if not usable:
            continue
        if "e" in caps:
            decrypt[keygrip] = key
        if "s" in caps:
            sign[keygrip] = key
    return KeyPolicy(decrypt=decrypt, sign=sign)


def load_policy() -> KeyPolicy:
    output = subprocess.run(
        ["gpg", "--batch", "--with-colons", "--with-keygrip", "-K"],  # noqa: S607
        capture_output=True,
        check=True,
        text=True,
    ).stdout
    return parse_card_keys(output)
