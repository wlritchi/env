{
  config,
  pkgs,
  lib,
  ...
}:

let
  secretive = pkgs.callPackage ./pkgs/secretive.nix { };
  age-with-plugins = import ./pkgs/age-with-plugins.nix { inherit pkgs; };
  browserpass-native-passage = import ./pkgs/browserpass-native-passage.nix {
    inherit pkgs age-with-plugins;
  };
  secwrap = import ./pkgs/secwrap.nix {
    inherit pkgs;
    backend = "passage";
  };
in
{
  imports = [
    ./common.nix
    ./launchd-services.nix
  ];

  home.username = "luc.ritchie";
  home.homeDirectory = "/Users/luc.ritchie";
  home.stateVersion = "25.11";

  fonts.fontconfig.enable = true;

  home.packages =
    (with pkgs; [
      # Fonts
      fira-code
      nerd-fonts.fira-code

      aerospace
      age-plugin-se
      age-with-plugins
      autokbisw
      bash
      coreutils-full
      ghostty-bin
      karabiner-elements
      passage
      procps # for the watch command
      secretive
    ])
    ++ [ secwrap ];

  # Keyboard layouts must be copied, not symlinked (macOS ignores symlinks)
  home.activation.copyKeyboardLayouts = lib.hm.dag.entryAfter [ "writeBoundary" ] ''
    run mkdir -p "$HOME/Library/Keyboard Layouts"
    run ${pkgs.rsync}/bin/rsync -aL --chmod=u+w \
      "${../keyboard}/" "$HOME/Library/Keyboard Layouts/"
  '';

  programs.browserpass = {
    enable = true;
    browsers = [
      "chrome"
      "librewolf"
    ];
    package = browserpass-native-passage;
  };
}
