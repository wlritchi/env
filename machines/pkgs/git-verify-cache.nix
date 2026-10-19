{
  lib,
  rustPlatform,
}:

rustPlatform.buildRustPackage {
  pname = "git-verify-cache";
  version = "0.1.0";

  src = ../../rust-utils/git-verify-cache;

  cargoLock = {
    lockFile = ../../rust-utils/git-verify-cache/Cargo.lock;
  };

  # The executable name selects the mode. The git config names these two.
  postInstall = ''
    ln -s git-verify-cache $out/bin/wlr-git-ssh-verify
    ln -s git-verify-cache $out/bin/wlr-git-gpg-verify
  '';

  meta = {
    description = "Cache git signature verification results from ssh-keygen and gpg";
    license = lib.licenses.mit;
    platforms = lib.platforms.unix;
    mainProgram = "git-verify-cache";
  };
}
