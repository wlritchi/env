{ config, pkgs, lib, ... }:

let
  pinDevspace = import (builtins.fetchTarball {
    url =
      "https://github.com/NixOS/nixpkgs/archive/5b5b46259bef947314345ab3f702c56b7788cab8.tar.gz";
    sha256 = "0sja5xnj2crjiw0p34hyb9f9v6nn8fzl7bhq8y250jgp0f0xll4n";
  }) { system = pkgs.system; };
  pinOllama = import (builtins.fetchTarball {
    url =
      "https://github.com/NixOS/nixpkgs/archive/b8ee30758753f81c0ee6848f1608e6418e36586e.tar.gz";
    sha256 = "1gah09dhlzarv0v95q60mpqpi1qal5sl0n0zwjf2lv2pwbzs3302";
  }) { system = pkgs.system; };
in {
  imports = [ ./common.nix ./launchd-services.nix ];

  home.username = "luc.ritchie";
  home.homeDirectory = "/Users/luc.ritchie";
  home.stateVersion = "25.05";

  home.packages = with pkgs; [
    aerospace
    awscli2
    bash
    browserpass
    coreutils-full
    docker
    openssh
    pinentry_mac
    teleport
    pinDevspace.devspace
    pinOllama.ollama
  ];
}
