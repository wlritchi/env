# Forwarded `pass` decryption with verified prompts

## Goal

Run `pass show` on a remote machine over SSH and approve each decryption with a touch on the local
YubiKey, as with agent forwarding. Keep these properties:

- Every decryption needs a YubiKey touch (the card has `UIF Decrypt=on`).
- PIN entry, when needed, happens on the local machine.
- The local machine shows which entry is being decrypted and which machine asks for it.
- The local machine verifies both of these. It does not trust the remote's claims.

The local machine and its copy of the password store are trusted. A remote machine can be
compromised.

## Current state

- The store (`~/.password-store`, origin `git.wlritchi.com/wlritchi/pass`) is encrypted to
  `C588A5E27B6C869860FCF045876A015AE96798A7`. The encryption subkey (cv25519) is on the YubiKey
  OpenPGP applet.
- Store commits are signed: with the GPG signing subkey on local sessions, and with the forwarded
  SSH `sk` key in `sshx` sessions (`gpg.format=ssh`).
- `pinentry-wayprompt` handles PIN prompts.
- gpg-agent on amygdalin runs `S.gpg-agent.extra` through systemd socket activation.

## Level 0: stock gpg-agent forwarding (tested on neon, 2026-10-09)

gpg-agent's extra socket is made for forwarding. A client on it is in restricted mode.

Remote setup, one time:

```sh
install -d -m700 ~/.gnupg-fwd
printf 'no-autostart\n' > ~/.gnupg-fwd/gpg.conf
gpg --export C588A5E27B6C869860FCF045876A015AE96798A7 \
    | ssh REMOTE 'GNUPGHOME=~/.gnupg-fwd gpg --batch --import'
GNUPGHOME=~/.gnupg-fwd gpgconf --create-socketdir
```

A separate `GNUPGHOME` keeps the remote's own gpg-agent and keyboxd running, so the remote can still
use a local YubiKey when you sit at it. The agent socket path depends on `GNUPGHOME`
(`/run/user/1000/gnupg/d.<hash>/S.gpg-agent`).

Per session:

```sh
ssh -O forward -R /run/user/1000/gpg-fwd.sock:/run/user/1000/gnupg/S.gpg-agent.extra REMOTE
# On the remote, one time:
ln -s /run/user/1000/gpg-fwd.sock "$(GNUPGHOME=~/.gnupg-fwd gpgconf --list-dirs agent-socket)"
# On the remote:
GNUPGHOME=~/.gnupg-fwd pass show ENTRY
```

Results:

- `gpg -K` on the remote shows the card subkeys (`ssb>`). No secret key stubs are necessary.
- `pass show` works. The local YubiKey asks for a touch and the PIN prompt is local.
- The local prompts give no information about the entry or the remote machine.

Gotchas found during the test:

- `gpg-connect-agent` and `gpgconf` do not read `gpg.conf`, so they ignore `no-autostart`. When the
  forwarded socket is missing, they start a gpg-agent in `~/.gnupg-fwd`. That agent then holds the
  socket path and blocks the forward. Use `gpg-connect-agent --no-autostart`, and kill a stray agent
  if one starts.
- `ssh -O cancel -R <unix>:<unix>` fails on a ControlMaster connection with "port not forwarded".
  If a forward is added again with the same arguments, the master treats it as a duplicate and does
  not bind it again, even when the remote socket file was deleted. Forward to a new remote path, or
  open a new master connection.
- A stale socket file on the remote blocks a new forward unless the remote sshd has
  `StreamLocalBindUnlink yes`. Without that, delete the stale file before `ssh -O forward`.
- The remote socket path uses the remote UID, which ssh cannot expand. All machines use UID 1000
  now.

## Level 1+2: verifying proxy

A local daemon, `pass-fwd`, sits between the forwarded socket and `S.gpg-agent.extra`. It passes the
Assuan protocol through, applies an allowlist, and intercepts `PKDECRYPT`.

### Which machine

`pass-fwd` listens on one local socket per host: `$XDG_RUNTIME_DIR/pass-fwd/<host>.sock`. `sshx`
asks the daemon for the socket for the destination host, then forwards it. A request on
`neon.sock` can only come through an SSH connection to neon that this machine opened. The ssh client
already verified the host key of that connection, so the host name comes from the local side, not
from the remote.

This proves the SSH session, not the process. Root, or any process of the same user on the remote,
can use the socket while the session is open. This is the same as for ssh-agent forwarding.

For hosts reached without `sshx`, ssh_config can do the same with `RemoteForward` and `%n` in the
local socket path, if the daemon makes a socket for each configured host.

### Which entry

For each recipient, OpenPGP makes a new ECDH key pair. The public key packet of an entry (PKESK)
holds the ephemeral point `e` and the wrapped session key `s`, so these values are different for
each encrypted file.

During `PKDECRYPT`, gpg sends this S-expression after the `INQUIRE CIPHERTEXT`:

```
(7:enc-val(4:ecdh(1:s49:<len byte + 48-byte wrapped key>)(1:e33:<0x40 + 32-byte X25519 point>)))
```

The test on neon captured this value for `Throwaway/opensubtitles.com`. Its `e` and `s` bytes occur
together at offsets 14 and 47 of the local file, which is the PKESK. No other file in the store
contains that `e`.

The daemon keeps an index from `sha256(e || s)` to entry paths. It builds the index from the local
checkout and updates it when the store's git refs change. On each `PKDECRYPT`:

- One match: the prompt shows that path. The session key that the agent returns opens only that
  file, so the path is correct, whatever the remote says.
- No match: the prompt says "unknown ciphertext" and refuses. A possible extension: fetch origin,
  index only commits with a valid signature from a trusted key (GPG signing subkey or the `sk` SSH
  keys), and look again.
- Two or more paths: refuse and report a possible attack. A machine that can push to origin can
  copy the PKESK of `Finance/bank` into a file with a harmless name. Detection of the duplicate
  stops this, if the index already knows the original.

The index can also include old versions of entries from git history, with labels such as
`Finance/bank (commit abc123)`.

### Prompt and approval

For each `PKDECRYPT`, `pass-fwd`:

1. Shows "neon wants `Finance/bank` — touch YubiKey to approve" as a desktop notification.
2. Sends the request to the agent. The touch is the approval. If no touch occurs, the card times out
   and the request fails.
3. Removes the notification when the agent replies.

The daemon sends one `PKDECRYPT` at a time, so the notification always matches the operation that
waits on the card. A local `pass` decryption at the same time does not go through the daemon and
can still race with it.

PIN prompts come from gpg-agent and `pinentry-wayprompt` as before.

### Command allowlist

These commands occurred in the neon test and must pass:

`RESET`, `OPTION`, `GETINFO restricted`, `GETINFO version`, `SCD SERIALNO`,
`SCD KEYINFO --list=encr`, `HAVEKEY`, `SETKEY`, `SETKEYDESC`, `PKDECRYPT` (with its inquire data),
`NOP`, `BYE`.

The daemon refuses other commands. The agent already refuses some of them in restricted mode.

`SETKEYDESC` text comes from the remote. The agent shows it only for soft keys, not for card PIN
prompts. The daemon can replace it with its own verified text.

`PKSIGN` is refused by default. The daemon sees only a digest, so it cannot show what is signed.
Remote `pass` commits in `sshx` sessions sign with SSH, so they do not need `PKSIGN`.

## passage variant

The same design works with passage and `age-plugin-yubikey` (PIV applet, touch policy `always`,
PIN policy `once`). age has no agent, so the remote uses a plugin, `age-plugin-forward`, as its
identity. The plugin sends the `piv-p256` stanza of the file to the forwarded socket. The daemon
finds the stanza in its index, unwraps it locally with `age-plugin-yubikey`, and returns the file
key. Each stanza has its own ephemeral share, so the index works the same way.

Migrate to passage only to share one store between the YubiKey on Linux and the Secure Enclave on
macOS (`age-plugin-se`). Forwarding does not require it.

## Plan

1. Level 0 tooling: a remote setup command for `~/.gnupg-fwd` and the socket symlink, and forwarding
   in `sshx`. `sshx` also sets `GNUPGHOME` (or `PASSWORD_STORE_GPG_OPTS`) on the remote when the
   forward is active.
2. `pass-fwd` daemon: per-host sockets, allowlist, `PKDECRYPT` index, notification, one request at a
   time. Python, in `src/wlrenv/`, run as a systemd user service.
3. Fetch on index miss, with commit signature checks.
4. Optional: passage variant.

## Open questions

- Notification only, or a confirm dialog before the touch? A confirm dialog adds a step, and
  it can collide with a PIN dialog from the agent.
- Should `PKSIGN` be possible with a prompt, for remote GPG signing?
- Is the passage store on macOS separate from this store, and should the two merge?

## Related issue

The `sshx` forward for `ssh-sk-helper` uses a random TCP port on the remote's localhost. Any user on
the remote can connect to it (each operation still needs a touch), and the port is not checked for
collisions. A Unix socket forward, as above, fixes both.
