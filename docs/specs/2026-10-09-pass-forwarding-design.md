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
- The forward closes with its master connection. After a reconnect, the remote has a stale socket
  file and no forward, and gpg reports "No agent running". `sshx` must set up the forward on each
  new connection.
- The remote socket path uses the remote UID, which ssh cannot expand. All machines use UID 1000
  now.

## Level 1+2: verifying proxy

A local daemon, `pass-fwd`, sits between the forwarded socket and `S.gpg-agent.extra`. It passes the
Assuan protocol through, applies an allowlist, and intercepts `PKDECRYPT` and `PKSIGN`.

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

The daemon shows a confirm dialog before the agent receives the request. Thus the agent cannot show
a PIN dialog, and the card cannot wait for a touch, before you approve.

For `PKDECRYPT`, the daemon does not send the command to the agent. It acts as the agent for this
step:

1. It sends `S INQUIRE_MAXLEN 4096` and `INQUIRE CIPHERTEXT` to the client, and reads the `D` lines
   until `END`.
2. It finds the entry in the index, and shows the dialog: "neon wants to decrypt `Finance/bank`.
   After you approve, touch the YubiKey."
3. On Approve, it sends `PKDECRYPT` to the agent, answers the agent's `INQUIRE CIPHERTEXT` with the
   data from step 1, and relays the replies of the agent to the client. Then the agent asks for the
   PIN if necessary, and the card waits for a touch.
4. On Deny or timeout, it sends `ERR 83886179 Operation cancelled <Pinentry>` to the client. The
   agent receives nothing.

The commands before `PKDECRYPT` (`SETKEY`, `HAVEKEY`, `SCD SERIALNO`) do not cause a PIN dialog or a
touch, so they can go to the agent immediately.

The dialog uses `pinentry-wayprompt` through the pinentry protocol: `SETTITLE`, `SETDESC`, `SETOK`,
`SETCANCEL`, and `CONFIRM`. Tested on wayprompt 0.1.2: these work, but `SETTIMEOUT` returns "Not
implemented". The daemon stops the pinentry process after 60 seconds and treats this as Deny.

The daemon shows one dialog and runs one card operation at a time. Other requests wait in a queue.
A local `pass` decryption does not go through the daemon. It can still ask for a touch at the same
time as an approved remote request.

### Signing

The daemon also lets a remote machine sign with the YubiKey signing subkey, after a confirm dialog.
The neon test showed this sequence:

```
SCD GETATTR KEY-FPR
READKEY --card --no-data -- $SIGNKEYID   (the agent refuses this in restricted mode)
SIGKEY <keygrip>
SETKEYDESC <text>
SETHASH 10 <SHA-512 digest, hex>
PKSIGN
```

`PKSIGN` has no inquire. The daemon holds `PKSIGN`, shows the dialog, and sends `PKSIGN` to the
agent only on Approve.

The daemon accepts `SIGKEY` only for the keygrip of the signing subkey (`E6542F8D51DCCF82`). It
finds the keygrip with `gpg --with-keygrip -K` at start. The primary key is not on the card, so a
remote machine cannot make key certifications.

**The local machine cannot tell what it signs.** The agent receives only a digest. A git commit, a
tag, an email, or a signed file look the same. The dialog must say this clearly, for example:

> neon wants a signature with your OpenPGP signing key (E654 2F8D 51DC CF82).
>
> **This machine cannot see what will be signed.** Approve only if you started a signing operation
> on neon just now.
>
> SHA-512: 83A4 398E 9FCD A833 …

The dialog uses a different title and button text from the decrypt dialog ("Sign", not "Approve"),
so that you do not approve a signature by habit.

A signature has more effect than a decryption. The index can trust fetched commits because they
have your signature. If you approve a signature that a compromised remote asks for, the remote can
make a signed commit that the index trusts.

### Command allowlist

These commands occurred in the neon tests and must pass:

`RESET`, `OPTION`, `GETINFO restricted`, `GETINFO version`, `SCD SERIALNO`,
`SCD KEYINFO --list=encr`, `SCD GETATTR KEY-FPR`, `READKEY`, `HAVEKEY`, `SETKEY`, `SIGKEY` (signing
subkey only), `SETKEYDESC`, `SETHASH`, `PKDECRYPT` (held for the dialog), `PKSIGN` (held for the
dialog), `NOP`, `BYE`.

The daemon refuses other commands. The agent already refuses some of them in restricted mode.

`SETKEYDESC` text comes from the remote. The agent shows it only for soft keys, not for card PIN
prompts. The daemon replaces it with its own text.

## passage variant

The same design works with passage and `age-plugin-yubikey` (PIV applet, touch policy `always`,
PIN policy `once`). age has no agent, so the remote uses a plugin, `age-plugin-forward`, as its
identity. The plugin sends the `piv-p256` stanza of the file to the forwarded socket. The daemon
finds the stanza in its index, shows the same confirm dialog, unwraps the stanza locally with
`age-plugin-yubikey`, and returns the file key. Each stanza has its own ephemeral share, so the
index works the same way.

This store stays on pass for now, because Android Password Store does not support passage. The
long-term plan is to move to age. Thus keep the index, the per-host sockets, and the dialog
separate from the Assuan code, so that the age variant can use them.

## Plan

1. Level 0 tooling: a remote setup command for `~/.gnupg-fwd` and the socket symlink, and forwarding
   in `sshx` on each new connection. `sshx` also sets `GNUPGHOME` (or `PASSWORD_STORE_GPG_OPTS`) on
   the remote when the forward is active.
2. `pass-fwd` daemon: per-host sockets, allowlist, `PKDECRYPT` index, confirm dialogs for decryption
   and signing, one request at a time. Python, in `src/wlrenv/`, run as a systemd user service.
3. Fetch on index miss, with commit signature checks.
4. Later: passage variant, when the store moves to age.

## Decisions (2026-10-09)

- A confirm dialog comes before the agent receives the request. No notification-only mode.
- Remote signing is allowed with the signing subkey only, behind a dialog that says the local machine
  cannot see what is signed.
- The store stays on pass until Android Password Store supports passage.

## Implementation (2026-10-09)

Steps 1 and 2 of the plan are done: `src/wlrenv/pass_fwd/`, the `wlr-pass-fwd` command,
`dotfiles/.config/systemd/user/pass-fwd.service`, and `bin/ssh/sshx`. Differences from the
design above:

- The remote socket is `/tmp/pass-fwd.<random>.sock`, new for each connection, so that a stale
  socket cannot block the bind. `sshx` points the agent socket of `~/.gnupg-fwd` at it and removes
  both when the session ends.
- `sshx` sets `PASSWORD_STORE_GPG_OPTS="--homedir $HOME/.gnupg-fwd"`, not `GNUPGHOME`, so only
  pass uses the forward. `tmux` copies this variable into new and attached sessions
  (`update-environment` in `dotfiles/.tmux.conf`). Without that, a remote `tmux new-session` gets
  the environment of the tmux server.
- The daemon accepts only the card slots whose touch policy is `on` or `permanent`
  (`gpg --card-status`). It refuses keys on disk, the `cached` touch modes, and other cards.
- The ciphertext must have the exact form `(enc-val (ecdh (s ...) (e ...)))`. A form with
  repeated or extra elements could make the daemon and gpg-agent read different values.
- `pinentry-wayprompt` shows the refusal message for an unknown ciphertext. It has a button only
  if `SETOK` is set. Refusal messages wait in the dialog queue, and only one can be pending.
- `wlr-pass-fwd setup-remote HOST` makes `~/.gnupg-fwd` and imports the public keys and owner
  trust of all recipients in the store.

## Related issue

The `sshx` forward for `ssh-sk-helper` uses a random TCP port on the remote's localhost. Any user on
the remote can connect to it (each operation still needs a touch), and the port is not checked for
collisions. A Unix socket forward, as above, fixes both.
