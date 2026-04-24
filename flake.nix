{
  description = "home-manager config";
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/release-25.11";
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
      url = "github:nix-community/home-manager/release-25.11";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    krew2nix = {
      url = "github:wlritchi/krew2nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    nix-darwin = {
      url = "github:nix-darwin/nix-darwin/nix-darwin-25.11";
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
      allowUnfreePredicate =
        pkg:
        let
          name = nixpkgs.lib.getName pkg;
        in
        builtins.elem name [
          # list of packages
        ];

      aerospaceOverlay = final: prev: {
        aerospace = prev.aerospace.overrideAttrs (oldAttrs: rec {
          version = "0.20.2-Beta";
          src = prev.fetchzip {
            url = "https://github.com/nikitabobko/AeroSpace/releases/download/v${version}/AeroSpace-v${version}.zip";
            sha256 = "sha256-PyWHtM38XPNkkEZ0kACPia0doR46FRpmSoNdsOhU4uw=";
          };
        });
      };

      # Pin bun to 1.3.13 until nixpkgs#508770 lands; required for Claude Code
      # v2.1.113+ post-install hook compatibility. Override `src` directly
      # rather than `passthru.sources` because release-25.11's bun uses
      # `mkDerivation rec`, which freezes `src` against pre-override scope.
      bunOverlay = final: prev: {
        bun = prev.bun.overrideAttrs (oldAttrs: {
          version = "1.3.13";
          __intentionallyOverridingVersion = true;
          src =
            {
              "aarch64-darwin" = prev.fetchurl {
                url = "https://github.com/oven-sh/bun/releases/download/bun-v1.3.13/bun-darwin-aarch64.zip";
                hash = "sha256-VGfj9l26Umuf6pjwzOBO+vwMY+Fpcz7Ce4dqOtMtoZA=";
              };
              "aarch64-linux" = prev.fetchurl {
                url = "https://github.com/oven-sh/bun/releases/download/bun-v1.3.13/bun-linux-aarch64.zip";
                hash = "sha256-cLrkGzkIsKEg4eWMXIrzDnSvrjuNEbDT/djnh937SyI=";
              };
              "x86_64-darwin" = prev.fetchurl {
                url = "https://github.com/oven-sh/bun/releases/download/bun-v1.3.13/bun-darwin-x64-baseline.zip";
                hash = "sha256-qYumpIDyL9qbNDYmuQak4mqlNhi/hdK8WSjs8rpF8O0=";
              };
              "x86_64-linux" = prev.fetchurl {
                url = "https://github.com/oven-sh/bun/releases/download/bun-v1.3.13/bun-linux-x64.zip";
                hash = "sha256-ecB3H6i5LDOq5B4VoODTB+qZ0OLwAxfHHGxTI3p44lo=";
              };
            }
            .${prev.stdenvNoCC.hostPlatform.system}
              or (throw "Unsupported system for bun override: ${prev.stdenvNoCC.hostPlatform.system}");
        });
      };

      overlays = [
        aerospaceOverlay
        bunOverlay
      ];

      mkPkgs =
        system:
        import nixpkgs {
          inherit system;
          config = { inherit allowUnfreePredicate; };
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
          env = builtins.getEnv "USER";
        in
        if env != "" then env else "unknown";

      pkgs = mkPkgs system;
      isDarwin = builtins.match ".*-darwin" system != null;
      platformModule = if isDarwin then ./machines/darwin.nix else ./machines/linux.nix;
    in
    {
      lib = { inherit allowUnfreePredicate overlays; };

      homeConfigurations.default = home-manager.lib.homeManagerConfiguration {
        inherit pkgs;
        modules = [ platformModule ];
        extraSpecialArgs = {
          inherit
            hostname
            username
            krew2nix
            try
            ;
        };
      };

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
