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
  claude-code = pkgs.callPackage ./pkgs/claude-code.nix { };
  claude-code-variant = pkgs.callPackage ./pkgs/claude-code-variant.nix { };
  claude-code-kimi = pkgs.callPackage ./pkgs/claude-code-kimi.nix {
    inherit claude-code-variant;
    claude-code-bin = pkgs.callPackage ./pkgs/claude-code.nix {
      brand = "kimi";
      brandSplash = ./pkgs/cc-kimi-splash.txt;
    };
  };
  claude-code-zai = pkgs.callPackage ./pkgs/claude-code-zai.nix {
    inherit claude-code-variant;
    claude-code-bin = pkgs.callPackage ./pkgs/claude-code.nix {
      brand = "zai";
      brandSplash = ./pkgs/cc-zai-splash.txt;
    };
  };
  claude-code-minimax = pkgs.callPackage ./pkgs/claude-code-minimax.nix {
    inherit claude-code-variant;
    claude-code-bin = pkgs.callPackage ./pkgs/claude-code.nix {
      brand = "minimax";
      brandSplash = ./pkgs/cc-minimax-splash.txt;
    };
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
        claude-code-kimi
        claude-code-minimax
        claude-code-zai
        delta-realpath
        entire
        tryPkg
      ];

    home.file = lib.mkMerge [
      {
        # Claude Code's startup "doctor" checks for a native install at
        # ~/.local/bin/claude and warns if it's missing/broken, even though we run
        # it from the nix profile. Point that canonical path at the profile binary
        # (out-of-store so it tracks the live profile, not a pinned store path) to
        # satisfy the check. mkOutOfStoreSymlink keeps it from dangling on version
        # bumps + GC, unlike a resolved dotfile symlink.
        ".local/bin/claude".source =
          config.lib.file.mkOutOfStoreSymlink "${config.home.profileDirectory}/bin/claude";
      }
      # Each provider variant ships its theme + managed settings (theme selection
      # and blocked tools) as read-only store symlinks under ~/.cc-<name>/,
      # replacing the old per-launch wrapper writes.
      claude-code-kimi.homeFiles
      claude-code-zai.homeFiles
      claude-code-minimax.homeFiles
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
