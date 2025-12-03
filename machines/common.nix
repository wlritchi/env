{ config, pkgs, krew2nix, ... }:

{
  home.packages = (with pkgs; [
    bat
    bun
    csvq
    delta
    eza
    fnm
    fzf
    gh
    git-lfs
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
    sccache
    stylua
    tmux
    watchexec
    zellij
    zoxide
  ]) ++ [
    (krew2nix.packages.${pkgs.system}.kubectl.withKrewPlugins
      (plugins: [ plugins.ctx plugins.ns plugins.rabbitmq plugins.rook-ceph ]))
  ];

  programs.home-manager.enable = true;

  nix = {
    package = pkgs.nix;
    settings = {
      experimental-features = [ "nix-command" "flakes" ];
      # Optimize build parallelism
      # cores = 0 means use all available cores for each build job
      cores = 0;
      # max-jobs = auto scales with available CPU cores
      # Set to a reasonable number to avoid overwhelming the system
      max-jobs = "auto";
    };
  };
}
