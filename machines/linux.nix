{ config, pkgs, lib, hostname ? "default", ... }:

let
  hostModule = ./hosts + "/${hostname}.nix";
  hostImports = lib.optional (builtins.pathExists hostModule) hostModule;
in {
  imports = [ ./common.nix ./systemd-services.nix ./librewolf-extension.nix ]
    ++ hostImports;

  home.username = "wlritchi";
  home.homeDirectory = "/home/wlritchi";
  home.stateVersion = "25.05";
}
