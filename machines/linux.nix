{
  config,
  pkgs,
  lib,
  hostname ? "default",
  username ? "wlritchi",
  ...
}:

let
  hostModule = ./hosts + "/${hostname}.nix";
  hostImports = lib.optional (builtins.pathExists hostModule) hostModule;
  niri-spacer = pkgs.callPackage ./pkgs/niri-spacer.nix { };
in
{
  imports = [
    ./common.nix
    ./systemd-services.nix
    ./librewolf-extension.nix
  ]
  ++ hostImports;

  custom.krewPlugins = [
    "modify-secret"
    "rabbitmq"
    "rook-ceph"
  ];

  home.packages =
    (with pkgs; [
      mold
      rclone
    ])
    ++ [
      niri-spacer
    ];

  home.username = username;
  home.homeDirectory = "/home/${username}";
  home.stateVersion = "25.11";
}
