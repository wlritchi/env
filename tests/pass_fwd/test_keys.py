from wlrenv.pass_fwd.keys import CardKey, KeyPolicy, PolicySource, parse_policy

LISTING = """\
sec:u:255:22:876A015AE96798A7:1620401901:::u:::cESCA:::#::ed25519::0:
fpr:::::::::C588A5E27B6C869860FCF045876A015AE96798A7:
grp:::::::::B7BD6F80A267151CF60DAC4C0557845C25651C62:
ssb:u:255:18:60F5539D01361413:1625443291:1814937117:::::e:::D2760001240100000006164941420000::cv25519::
fpr:::::::::8CADFC387A0FA73EB82BB55E60F5539D01361413:
grp:::::::::D6A566463C1D9CD21D6EFB77B1D3B766AA8713D0:
ssb:u:255:22:E6542F8D51DCCF82:1625443068::::::s:::D2760001240100000006164941420000::ed25519::
fpr:::::::::C219D06629A6E8A45B2642A1E6542F8D51DCCF82:
grp:::::::::57B7D28AC52C06DA42F5E0260679DF106DDEE770:
sec:u:255:22:D867F3B8D464C5D7:1775270121:1838342121::u:::scSC:::+::ed25519::0:
fpr:::::::::6F4F126EFCF0ADAEDFDD31FED867F3B8D464C5D7:
grp:::::::::AB5A232FAFA725F79788B6022A7C3772AC4DF9AB:
"""
FPR = (
    "fpr:C219D06629A6E8A45B2642A1E6542F8D51DCCF82:"
    "8CADFC387A0FA73EB82BB55E60F5539D01361413:"
    "593BB2553E44FCEA8A04E7D3716851EA13DAF7A8:"
)
DECRYPT = CardKey("60F5539D01361413", "D6A566463C1D9CD21D6EFB77B1D3B766AA8713D0")
SIGN = CardKey("E6542F8D51DCCF82", "57B7D28AC52C06DA42F5E0260679DF106DDEE770")


def status(uif: str) -> str:
    return f"serial:16494142:\nuif:{uif}:\n{FPR}\n"


def test_touch_on_for_both_slots() -> None:
    assert parse_policy(status("1:1:1"), LISTING) == KeyPolicy(
        decrypt={DECRYPT.keygrip: DECRYPT}, sign={SIGN.keygrip: SIGN}
    )


def test_cached_touch_is_excluded() -> None:
    assert parse_policy(status("1:3:1"), LISTING) == KeyPolicy(
        sign={SIGN.keygrip: SIGN}
    )


def test_touch_off_is_excluded() -> None:
    assert parse_policy(status("0:2:1"), LISTING) == KeyPolicy(
        decrypt={DECRYPT.keygrip: DECRYPT}
    )


def test_no_card() -> None:
    assert parse_policy("", LISTING).empty


def test_disk_key_in_slot_is_excluded() -> None:
    # The fingerprint in the slot belongs to a key on disk.
    card = "uif:1:1:1:\nfpr:6F4F126EFCF0ADAEDFDD31FED867F3B8D464C5D7::\n"
    assert parse_policy(card, LISTING) == KeyPolicy()


def test_source_reloads_while_empty() -> None:
    results = [KeyPolicy(), KeyPolicy(decrypt={DECRYPT.keygrip: DECRYPT})]
    calls: list[int] = []

    def loader() -> KeyPolicy:
        calls.append(1)
        return results[len(calls) - 1]

    source = PolicySource(loader)
    assert source().empty
    source.loaded_at -= 60
    assert not source().empty
    source.loaded_at -= 60
    assert not source().empty
    assert len(calls) == 2


def test_reload_replaces_a_loaded_policy() -> None:
    results = [
        KeyPolicy(decrypt={DECRYPT.keygrip: DECRYPT}),
        KeyPolicy(),
    ]
    source = PolicySource(lambda: results.pop(0))
    assert not source().empty
    assert source.reload().empty
    assert source().empty
