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
  secwrap = import ./pkgs/secwrap.nix {
    inherit pkgs;
    backend = "pass";
  };
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
      secwrap
    ];

  home.username = username;
  home.homeDirectory = "/home/${username}";
  home.stateVersion = "25.11";
}
