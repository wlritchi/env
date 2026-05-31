# Private home-manager module, added to the public config via `extraModules` in
# flake.nix. It is a peer of machines/linux.nix / machines/darwin.nix and
# receives the same arguments, including the specialArgs the public flake passes
# (hostname, username, krew2nix, try). Pull only what you use out of the set.
{
  config,
  pkgs,
  lib,
  hostname ? "default",
  username ? "wlritchi",
  ...
}:

{
  # Work-specific packages. The `++ lib.optionals` arm shows per-host gating via
  # the `hostname` specialArg — keep it one `home.packages` assignment (defining
  # the key twice in a single module is a Nix error; merging across modules is
  # what's allowed).
  home.packages =
    (with pkgs; [
      # awscli2
      # teleport
    ])
    ++ lib.optionals (hostname == "work-laptop") (
      with pkgs;
      [
        # slack
      ]
    );

  # Add / override / disable declarative uv tools. The `home.uvTools` option is
  # defined in the public machines/uv-tools.nix; see
  # docs/specs/2026-05-06-uv-tools-declarative-install.md.
  # home.uvTools.aider = { spec = "aider-chat"; };       # add a private tool
  # home.uvTools.hyfetch.python = "3.12";                # override one field
  # home.uvTools.hyfetch.disabled = true;                # suppress a public tool

  # Extend krew plugins (the `custom.krewPlugins` option is defined in the
  # public machines/common.nix; lists merge across modules).
  # custom.krewPlugins = [ "some-plugin" ];
}
