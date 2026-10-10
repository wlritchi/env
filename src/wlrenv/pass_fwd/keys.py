"""Find the keygrips of the smartcard keys that remote machines may use."""

import logging
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# A token serial field of "#" means that the key is not available, and "+" means
# that the key is on disk. A card key has the card serial number.
NOT_ON_CARD = {"", "#", "+"}
UNUSABLE_VALIDITY = {"e", "r", "d", "i"}
# OpenPGP card touch policy: 0 off, 1 on, 2 permanent, 3 cached, 4 cached
# permanent. The cached modes accept more operations for some seconds after one
# touch, so they do not need a touch for each decryption.
TOUCH_EACH_TIME = {"1", "2"}
SIGN_SLOT = 0
DECRYPT_SLOT = 1
RELOAD_INTERVAL = 10.0


@dataclass(frozen=True)
class CardKey:
    key_id: str
    keygrip: str


@dataclass(frozen=True)
class KeyPolicy:
    """Map keygrips to the card keys that may decrypt or sign."""

    decrypt: dict[str, CardKey] = field(default_factory=dict)
    sign: dict[str, CardKey] = field(default_factory=dict)

    @property
    def empty(self) -> bool:
        return not self.decrypt and not self.sign


def card_keys(listing: str) -> dict[str, CardKey]:
    """Map fingerprints to the usable smartcard keys in
    `gpg -K --with-keygrip --with-colons` output."""
    keys: dict[str, CardKey] = {}
    record: list[str] | None = None
    fingerprint: str | None = None
    for line in listing.splitlines():
        fields = line.split(":")
        if fields[0] in ("sec", "ssb"):
            record = fields
            fingerprint = None
        elif fields[0] == "fpr" and record is not None and fingerprint is None:
            fingerprint = fields[9].upper()
        elif fields[0] == "grp" and record is not None and fingerprint is not None:
            serial = record[14] if len(record) > 14 else ""
            if record[1] not in UNUSABLE_VALIDITY and serial not in NOT_ON_CARD:
                keys[fingerprint] = CardKey(record[4].upper(), fields[9].upper())
            record = None
    return keys


def parse_policy(card_status: str, listing: str) -> KeyPolicy:
    """Return the keys of the inserted card that need a touch for each use.

    `card_status` is `gpg --card-status --with-colons` output. A remote machine
    must not use keys on disk or card keys without a touch, because these do not
    let the user approve each operation.
    """
    touch: list[str] = []
    slots: list[str] = []
    for line in card_status.splitlines():
        fields = line.split(":")
        if fields[0] == "uif":
            touch = fields[1:4]
        elif fields[0] == "fpr":
            slots = [f.upper() for f in fields[1:4]]
    keys = card_keys(listing)

    def slot_key(slot: int) -> dict[str, CardKey]:
        if len(touch) <= slot or len(slots) <= slot:
            return {}
        if touch[slot] not in TOUCH_EACH_TIME:
            log.warning("card slot %d does not need a touch for each use", slot + 1)
            return {}
        key = keys.get(slots[slot])
        return {key.keygrip: key} if key else {}

    return KeyPolicy(decrypt=slot_key(DECRYPT_SLOT), sign=slot_key(SIGN_SLOT))


def run_gpg(*args: str) -> str:
    result = subprocess.run(  # noqa: S603
        ["gpg", "--batch", "--with-colons", *args],  # noqa: S607
        capture_output=True,
        text=True,
    )
    return result.stdout if result.returncode == 0 else ""


def load_policy() -> KeyPolicy:
    return parse_policy(run_gpg("--card-status"), run_gpg("--with-keygrip", "-K"))


class PolicySource:
    """Load the policy, and load it again while no card key is known."""

    def __init__(self, loader: Callable[[], KeyPolicy] = load_policy) -> None:
        self.loader = loader
        self.policy = KeyPolicy()
        self.loaded_at = float("-inf")

    def __call__(self) -> KeyPolicy:
        if self.policy.empty and time.monotonic() - self.loaded_at >= RELOAD_INTERVAL:
            return self.reload()
        return self.policy

    def reload(self) -> KeyPolicy:
        self.loaded_at = time.monotonic()
        self.policy = self.loader()
        if self.policy.empty:
            log.warning("no smartcard key with touch for each use is available")
        return self.policy
