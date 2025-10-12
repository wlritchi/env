{
  description = "home-manager config";
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/release-25.05";
    home-manager = {
      url = "github:nix-community/home-manager/release-25.05";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    krewfile = {
      url = "github:brumhard/krewfile";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };
  outputs = { nixpkgs, home-manager, krewfile, ... }: {
    homeConfigurations = {
      wlritchi = home-manager.lib.homeManagerConfiguration {
        pkgs = nixpkgs.legacyPackages.x86_64-linux;
        modules = [ ./machines/linux.nix krewfile.homeManagerModules.krewfile ];
        extraSpecialArgs = { hostname = "default"; };
      };
      "wlritchi@amygdalin" = home-manager.lib.homeManagerConfiguration {
        pkgs = nixpkgs.legacyPackages.x86_64-linux;
        modules = [ ./machines/linux.nix krewfile.homeManagerModules.krewfile ];
        extraSpecialArgs = { hostname = "amygdalin"; };
      };
      "luc.ritchie" = home-manager.lib.homeManagerConfiguration {
        pkgs = nixpkgs.legacyPackages.aarch64-darwin;
        modules =
          [ ./machines/darwin.nix krewfile.homeManagerModules.krewfile ];
      };
    };
  };
}
