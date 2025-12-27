{
  description = "home-manager config";
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/release-25.11";
    home-manager = {
      url = "github:nix-community/home-manager/release-25.11";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    krew2nix = {
      url = "github:eigengrau/krew2nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };
  outputs =
    {
      nixpkgs,
      home-manager,
      krew2nix,
      ...
    }:
    {
      homeConfigurations = {
        wlritchi = home-manager.lib.homeManagerConfiguration {
          pkgs = nixpkgs.legacyPackages.x86_64-linux;
          modules = [ ./machines/linux.nix ];
          extraSpecialArgs = {
            hostname = "default";
            inherit krew2nix;
          };
        };
        "wlritchi@amygdalin" = home-manager.lib.homeManagerConfiguration {
          pkgs = nixpkgs.legacyPackages.x86_64-linux;
          modules = [ ./machines/linux.nix ];
          extraSpecialArgs = {
            hostname = "amygdalin";
            inherit krew2nix;
          };
        };
        "luc.ritchie" = home-manager.lib.homeManagerConfiguration {
          pkgs = nixpkgs.legacyPackages.aarch64-darwin;
          modules = [ ./machines/darwin.nix ];
          extraSpecialArgs = { inherit krew2nix; };
        };
      };
    };
}
