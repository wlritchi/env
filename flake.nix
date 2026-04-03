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

      systems = [
        "x86_64-linux"
        "aarch64-linux"
        "aarch64-darwin"
      ];

      # Generate homeConfigurations keyed as "name/system" for each supported system
      mkHome =
        {
          name,
          hostname ? "default",
        }:
        system:
        let
          platformModule =
            if builtins.match ".*-darwin" system != null then ./machines/darwin.nix else ./machines/linux.nix;
        in
        {
          name = "${name}/${system}";
          value = home-manager.lib.homeManagerConfiguration {
            pkgs = mkPkgs system;
            modules = [ platformModule ];
            extraSpecialArgs = {
              inherit hostname krew2nix try;
            };
          };
        };

      homeConfigs' = [
        { name = "wlritchi"; }
        {
          name = "wlritchi@amygdalin";
          hostname = "amygdalin";
        }
        { name = "luc.ritchie"; }
      ];

      homeConfigs = builtins.concatMap (cfg: map (mkHome cfg) systems) homeConfigs';
    in
    {
      lib = { inherit allowUnfreePredicate overlays; };

      homeConfigurations = builtins.listToAttrs homeConfigs;

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
