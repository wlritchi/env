{
  config,
  pkgs,
  lib,
  krew2nix,
  ...
}:

{
  options.custom.krewPlugins = lib.mkOption {
    type = lib.types.listOf lib.types.str;
    default = [ ];
    description = "List of krew plugin names to install";
  };

  config = {
    custom.krewPlugins = [
      "ctx"
      "ns"
    ];

    home.packages =
      (with pkgs; [
        bat
        bun
        csvq
        delta
        eza
        fnm
        fzf
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
        ripgrep
        rustup
        sccache
        stylua
        tmux
        uv
        watchexec
        zellij
        zoxide
      ])
      ++ [
        (krew2nix.packages.${pkgs.stdenv.hostPlatform.system}.kubectl.withKrewPlugins (
          plugins: map (name: plugins.${name}) config.custom.krewPlugins
        ))
      ];

    programs.home-manager.enable = true;

    programs.gh = {
      enable = true;
      extensions = [ pkgs.gh-poi ];
    };

    nix = {
      package = pkgs.nix;
      settings = {
        experimental-features = [
          "nix-command"
          "flakes"
        ];
        # Optimize build parallelism
        # cores = 0 means use all available cores for each build job
        cores = 0;
        # max-jobs = auto scales with available CPU cores
        # Set to a reasonable number to avoid overwhelming the system
        max-jobs = "auto";
      };
    };
  };
}
