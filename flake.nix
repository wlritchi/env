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

      overlays = [ aerospaceOverlay ];

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
