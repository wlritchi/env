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
  `$GNUPGHOME`. Results expire after a week. All other calls go to `gpg`.

Only successful results are kept. The cache is 256 append-only shard files in
`$XDG_CACHE_HOME/wlr-git-verify/`, which compact themselves above 64 KiB. It
is safe to delete.

`bin/git/` has fallbacks with the same names, for machines whose Nix profile
does not have this package yet. The Nix profile is earlier on PATH.

For debugging, `WLR_GIT_VERIFY_LOG=/some/file` records each hit and miss.
