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
      url = "github:eigengrau/krew2nix";
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

      mkPkgs =
        system:
        import nixpkgs {
          inherit system;
          config = { inherit allowUnfreePredicate; };
          overlays = [ aerospaceOverlay ];
        };

      pkgsLinux = mkPkgs "x86_64-linux";
      pkgsDarwin = mkPkgs "aarch64-darwin";
    in
    {
      lib = { inherit allowUnfreePredicate; };

      homeConfigurations = {
        wlritchi = home-manager.lib.homeManagerConfiguration {
          pkgs = pkgsLinux;
          modules = [ ./machines/linux.nix ];
          extraSpecialArgs = {
            hostname = "default";
            inherit krew2nix try;
          };
        };
        "wlritchi@amygdalin" = home-manager.lib.homeManagerConfiguration {
          pkgs = pkgsLinux;
          modules = [ ./machines/linux.nix ];
          extraSpecialArgs = {
            hostname = "amygdalin";
            inherit krew2nix try;
          };
        };
        "luc.ritchie" = home-manager.lib.homeManagerConfiguration {
          pkgs = pkgsDarwin;
          modules = [ ./machines/darwin.nix ];
          extraSpecialArgs = { inherit krew2nix try; };
        };
      };
      darwinConfigurations = {
        luc_ritchie = nix-darwin.lib.darwinSystem {
          system = "aarch64-darwin";
          modules = [
            nix-homebrew.darwinModules.nix-homebrew
            ./machines/darwin-system.nix
          ];
          specialArgs = {
            username = "luc.ritchie";
            inherit homebrew-cask homebrew-core;
          };
        };
      };
    };
}
