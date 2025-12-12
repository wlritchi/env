{ config, pkgs, lib, hostname ? "default", ... }:

let
  hostModule = ./hosts + "/${hostname}.nix";
  hostImports = lib.optional (builtins.pathExists hostModule) hostModule;
  niri-spacer = pkgs.callPackage ./pkgs/niri-spacer.nix { };
in {
  imports = [ ./common.nix ./systemd-services.nix ./librewolf-extension.nix ]
    ++ hostImports;

  home.packages = (with pkgs; [ mold ]) ++ [ niri-spacer ];

  home.username = "wlritchi";
  home.homeDirectory = "/home/wlritchi";
  home.stateVersion = "25.11";
}
