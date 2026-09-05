{
  config,
  pkgs,
  lib,
  krew2nix,
  try,
  ...
}:

let
  ccstatusline = pkgs.callPackage ./pkgs/ccstatusline.nix { };
  cc-openai-proxy = pkgs.callPackage ./pkgs/cc-openai-proxy.nix { };
  cc-openai-proxy-launcher = pkgs.callPackage ./pkgs/cc-openai-proxy-launcher.nix {
    inherit cc-openai-proxy;
  };
  claude-code = pkgs.callPackage ./pkgs/claude-code.nix {
    inherit cc-openai-proxy-launcher;
  };
  entire = pkgs.callPackage ./pkgs/entire.nix { };
  delta-realpath = import ./pkgs/delta-realpath.nix { inherit pkgs; };
  tryPkg = try.packages.${pkgs.stdenv.hostPlatform.system}.default;
in
{
  imports = [ ./uv-tools.nix ];

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
        cargo-audit
        cargo-deny
        csvq
        eza
        fd
        fnm
        fzf
        git
        git-lfs
        git-sync
        gnupg
        go
        gopls
        jq
        k9s
        moreutils
        ncdu
        neovim
        nixfmt
        nnn
        onefetch
        prek
        qmk
        ripgrep
        rustup
        sccache
        stylua
        tmux
        uv
        watchexec
        yq-go
        zellij
        zoxide
      ])
      ++ [
        (krew2nix.packages.${pkgs.stdenv.hostPlatform.system}.kubectl.withKrewPlugins (
          plugins: map (name: plugins.${name}) config.custom.krewPlugins
        ))
        ccstatusline
        claude-code
        delta-realpath
        entire
        tryPkg
      ];

    home.file.".local/bin/claude".source =
      # Claude Code's startup doctor checks this canonical native-install path.
      # Use the live profile path so version changes and garbage collection do not
      # leave a stale store symlink.
      config.lib.file.mkOutOfStoreSymlink "${config.home.profileDirectory}/bin/claude";

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
