{ config, pkgs, ... }:

{
  home.packages = with pkgs; [
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
    kubectl
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
    zoxide
  ];

  programs.home-manager.enable = true;

  nix = {
    package = pkgs.nix;
    settings.experimental-features = [ "nix-command" "flakes" ];
  };
}
