{
  description = "wlrenv private overlay";

  # The ONLY input is the public wlrenv flake, and its name MUST be `wlrenv`:
  # wlr-nix-rebuild builds this overlay with
  #   --override-input wlrenv path:$WLR_ENV_PATH
  # so at rebuild time `wlrenv` always resolves to your LOCAL ~/.wlrenv checkout
  # (no `nix flake update` needed after editing the public repo). The pinned rev
  # in flake.lock only matters for standalone `nix build` / `nix flake show`.
  inputs.wlrenv.url = "github:wlritchi/env";

  outputs =
    { wlrenv, ... }:
    {
      # wlr-nix-rebuild runs `home-manager switch --flake <repo>#default`, so the
      # home config MUST be `homeConfigurations.default`. mkHomeConfiguration is
      # exported by the public flake's `lib` specifically for overlays to extend.
      homeConfigurations.default = wlrenv.lib.mkHomeConfiguration {
        extraModules = [ ./private.nix ];

        # Uncomment to allow specific unfree packages your module pulls in.
        # mkHomeConfiguration accepts ONLY `extraModules` and
        # `extraUnfreePredicate` — passing any other named arg is a Nix error.
        # extraUnfreePredicate = pkg: builtins.elem (pkg.pname or pkg.name) [
        #   "slack"
        # ];
      };

      # macOS only: wlr-nix-rebuild also runs `darwin-rebuild ... #default`.
      # This passes the PUBLIC system config through unextended (the public lib
      # exposes no darwin helper yet). Your private HOME config still applies on
      # macOS via homeConfigurations.default above; only system-level macOS
      # config stays at the public default. Harmless on Linux: this attribute is
      # evaluated lazily and only ever read by darwin-rebuild on a Mac.
      darwinConfigurations = wlrenv.darwinConfigurations;
    };
}
