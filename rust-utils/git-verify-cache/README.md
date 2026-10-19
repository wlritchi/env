# git-verify-cache

Caches git signature verification results. Git runs `gpg.ssh.program` two
times and `gpg.program` one time for each signed commit that a format such as
`%G?` shows, so `glog`/`gtree` over a long history can take seconds.

The Nix package installs the binary as `wlr-git-ssh-verify` (`gpg.ssh.program`)
and `wlr-git-gpg-verify` (`gpg.program`). The name selects the mode.

- SSH mode caches `-Y verify`, `find-principals` and `check-novalidate` in the
  argument forms that git uses. Results do not expire: git gives
  `-Overify-time`, and the key includes the contents of the signature, the
  allowed signers file and the payload. All other calls, signing included, go
  to `wlr-git-ssh-sign`, which asks for confirmation.
- OpenPGP mode caches `--keyid-format=long --status-fd=1 --verify FILE -`. The
  key also includes file metadata for the keyring and trust database in
  `$GNUPGHOME`, so an import, a revocation or a trust change starts a new
  entry. Results expire after a week, or when the signature expires if that
  is earlier. A key that expires during that week shows as valid until the
  result expires. If `gpg.conf` or `common.conf` (in `$GNUPGHOME` or
  `/etc/gnupg`) could name keyring or trust database files outside
  `$GNUPGHOME`, nothing is cached. That includes abbreviated option names
  and meta-commands such as `[ignore]`. All other calls go to `gpg`.

Only successful results are kept. The cache is 256 append-only shard files in
`$XDG_CACHE_HOME/wlr-git-verify/`, which compact themselves above 64 KiB. A
reader stops at the first bad record and never looks for records inside one,
because stored output can contain text that the signer chose. The cache is
safe to delete.

`bin/git/` has fallbacks with the same names, for machines whose Nix profile
does not have this package yet. The Nix profile is earlier on PATH.

For debugging, `WLR_GIT_VERIFY_LOG=/some/file` records each hit and miss.
