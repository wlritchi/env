{ config, pkgs, krew2nix, ... }:

let niri-spacer = pkgs.callPackage ./pkgs/niri-spacer.nix { };
in {
  home.packages = (with pkgs; [
    bat
    delta
    eza
    fnm
    fzf
    gh
    git-sync
    go
    gopls
    jq
    k9s
    moreutils
    neovim
    nixfmt
    nnn
    onefetch
    rclone
    ripgrep
    rustup
    stylua
    tmux
    watchexec
    zellij
    zoxide
  ]) ++ [ niri-spacer ] ++ [
    (krew2nix.packages.${pkgs.system}.kubectl.withKrewPlugins
      (plugins: [ plugins.ctx plugins.ns plugins.rabbitmq plugins.rook-ceph ]))
  ];

  programs.home-manager.enable = true;

  nix = {
    package = pkgs.nix;
    settings.experimental-features = [ "nix-command" "flakes" ];
  };
}
