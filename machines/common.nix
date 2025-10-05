{ config, pkgs, ... }:

{
  home.packages = with pkgs; [
    bat
    delta
    eza
    fnm
    fzf
    gh
    go
    gopls
    jq
    k9s
    kubectl
    moreutils
    neovim
    nixfmt
    onefetch
    ripgrep
    rustup
    stylua
    tmux
    zoxide
  ];

  programs.home-manager.enable = true;

  nix = {
    package = pkgs.nix;
    settings.experimental-features = [ "nix-command" "flakes" ];
  };
}
