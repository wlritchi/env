{
  description = "home-manager config";
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/release-26.05";
    nix-homebrew.url = "github:zhaofengli/nix-homebrew";
    homebrew-cask = {
      url = "github:homebrew/homebrew-cask";
      flake = false;
    };
    homebrew-core = {
      url = "github:homebrew/homebrew-core";
      flake = false;
    };
    home-manager = {
      url = "github:nix-community/home-manager/release-26.05";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    krew2nix = {
      url = "github:wlritchi/krew2nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    nix-darwin = {
      url = "github:nix-darwin/nix-darwin/nix-darwin-26.05";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    try = {
      url = "github:tobi/try";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };
  outputs =
    {
      homebrew-cask,
      homebrew-core,
      home-manager,
      krew2nix,
      nixpkgs,
      nix-darwin,
      nix-homebrew,
      try,
      ...
    }:

    let
      aerospaceOverlay = final: prev: {
        aerospace = prev.aerospace.overrideAttrs (oldAttrs: rec {
          version = "0.20.2-Beta";
          src = prev.fetchzip {
            url = "https://github.com/nikitabobko/AeroSpace/releases/download/v${version}/AeroSpace-v${version}.zip";
            sha256 = "sha256-PyWHtM38XPNkkEZ0kACPia0doR46FRpmSoNdsOhU4uw=";
          };
        });
      };

      overlays = [
        aerospaceOverlay
      ];

      mkPkgs =
        {
          system,
          extraUnfreePredicate ? (_: false),
        }:
        import nixpkgs {
          inherit system;
          config.allowUnfreePredicate = extraUnfreePredicate;
          inherit overlays;
        };

      # Impure: resolve system, hostname, and username from environment
      system =
        let
          env = builtins.getEnv "NIX_SYSTEM";
        in
        if env != "" then env else builtins.currentSystem;
      hostname =
        let
          env = builtins.getEnv "NIX_HOSTNAME";
        in
        if env != "" then env else "default";
      username =
        let
          # Prefer SUDO_USER so that running e.g. `sudo darwin-rebuild` still
          # resolves to the invoking user. nix-homebrew refuses to run as root.
          sudoUser = builtins.getEnv "SUDO_USER";
          user = builtins.getEnv "USER";
        in
        if sudoUser != "" then
          sudoUser
        else if user != "" then
          user
        else
          "unknown";

      isDarwin = builtins.match ".*-darwin" system != null;
      platformModule = if isDarwin then ./machines/darwin.nix else ./machines/linux.nix;

      mkHomeConfiguration =
        {
          extraUnfreePredicate ? (_: false),
          extraModules ? [ ],
        }:
        home-manager.lib.homeManagerConfiguration {
          pkgs = mkPkgs {
            inherit system;
            # Always allow our build-time-patched Claude Code (the only unfree
            # package in the base config); compose with any predicate an overlay
            # repo extends us with.
            extraUnfreePredicate =
              pkg:
              nixpkgs.lib.hasPrefix "claude-code-patched" (nixpkgs.lib.getName pkg) || extraUnfreePredicate pkg;
          };
          modules = [ platformModule ] ++ extraModules;
          extraSpecialArgs = {
            inherit
              hostname
              username
              krew2nix
              try
              ;
          };
        };
    in
    {
      lib = {
        inherit
          mkPkgs
          mkHomeConfiguration
          overlays
          ;
      };

      # Re-export the CLI tools from the locked inputs so wlr-nix-rebuild runs
      # the same versions declared here, with flake.lock as the single source
      # of truth.
      packages.${system} = {
        home-manager = home-manager.packages.${system}.home-manager;
      }
      // nixpkgs.lib.optionalAttrs isDarwin {
        darwin-rebuild = nix-darwin.packages.${system}.darwin-rebuild;
      };

      homeConfigurations.default = mkHomeConfiguration { };

      darwinConfigurations.default = nix-darwin.lib.darwinSystem {
        inherit system;
        modules = [
          nix-homebrew.darwinModules.nix-homebrew
          ./machines/darwin-system.nix
        ];
        specialArgs = {
          inherit username homebrew-cask homebrew-core;
        };
      };
    };
}
