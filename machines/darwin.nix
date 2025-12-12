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
  # Pin acli 1.3.6 from nixpkgs PR #446714 - should land in first 2026 release
  pinAcli =
    import
      (builtins.fetchTarball {
        url = "https://github.com/NixOS/nixpkgs/archive/16745d3a32e5aae9ea551edfa71baf1ccd0084a6.tar.gz";
        sha256 = "176cvzkyismx6b7qrwqmy0s118fw1agdq7rnxv610qyrsvbpm7ks";
      })
      {
        system = pkgs.stdenv.hostPlatform.system;
        config.allowUnfreePredicate = pkg: builtins.elem (lib.getName pkg) [ "acli" ];
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
    awscli2
    bash
    browserpass
    coreutils-full
    docker
    docker-buildx
    openssh
    pinentry_mac
    procps # for the watch command
    teleport
    pinAcli.acli
    pinDevspace.devspace
    pinOllama.ollama
  ];

  home.file.".docker/cli-plugins/docker-buildx" = {
    source = "${pkgs.docker-buildx}/libexec/docker/cli-plugins/docker-buildx";
  };
}
