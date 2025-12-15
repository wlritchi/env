{
  config,
  pkgs,
  lib,
  ...
}:

let
  pinDevspace = import (builtins.fetchTarball {
    url = "https://github.com/NixOS/nixpkgs/archive/5b5b46259bef947314345ab3f702c56b7788cab8.tar.gz";
    sha256 = "0sja5xnj2crjiw0p34hyb9f9v6nn8fzl7bhq8y250jgp0f0xll4n";
  }) { system = pkgs.stdenv.hostPlatform.system; };
  pinOllama = import (builtins.fetchTarball {
    url = "https://github.com/NixOS/nixpkgs/archive/b8ee30758753f81c0ee6848f1608e6418e36586e.tar.gz";
    sha256 = "1gah09dhlzarv0v95q60mpqpi1qal5sl0n0zwjf2lv2pwbzs3302";
  }) { system = pkgs.stdenv.hostPlatform.system; };
  # Pin acli 1.3.7 from nixpkgs PR #446714 - should land in first 2026 release
  pinAcli =
    import
      (builtins.fetchTarball {
        url = "https://github.com/NixOS/nixpkgs/archive/10ad3da76c55f4de109c2fce388316b98d128e9b.tar.gz";
        sha256 = "15m6yqgr8cqbw12vqy66gdb639qp1fyvx88j93sdjwzj3ax4k8wi";
      })
      {
        system = pkgs.stdenv.hostPlatform.system;
        config.allowUnfreePredicate = pkg: builtins.elem (lib.getName pkg) [ "acli" ];
      };
  secretive = pkgs.callPackage ./pkgs/secretive.nix { };
  age-with-plugins = import ./pkgs/age-with-plugins.nix { inherit pkgs; };
  browserpass-native-passage = import ./pkgs/browserpass-native-passage.nix {
    inherit pkgs age-with-plugins;
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

  home.packages = with pkgs; [
    aerospace
    age-plugin-se
    age-with-plugins
    autokbisw
    awscli2
    bash
    coreutils-full
    docker
    docker-buildx
    ghostty-bin
    karabiner-elements
    passage
    procps # for the watch command
    secretive
    teleport
    pinAcli.acli
    pinDevspace.devspace
    pinOllama.ollama
  ];

  home.file.".docker/cli-plugins/docker-buildx" = {
    source = "${pkgs.docker-buildx}/libexec/docker/cli-plugins/docker-buildx";
  };

  programs.browserpass = {
    enable = true;
    browsers = [
      "chrome"
      "librewolf"
    ];
    package = browserpass-native-passage;
  };
}
