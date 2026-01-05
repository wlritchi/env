{
  username,
  homebrew-core,
  homebrew-cask,
  ...
}:
{
  # Set once, do not change without reading all relevant news via `darwin-rebuild changelog`
  system.stateVersion = 6;

  # Don't manage nix itself
  nix.enable = false;

  # Enable TouchID for sudo authentication
  security.pam.services.sudo_local.touchIdAuth = true;

  # Set the primary user
  system.primaryUser = username;

  # Configure nix-homebrew
  nix-homebrew = {
    enable = true;
    user = username;
    mutableTaps = false;
    taps = {
      "homebrew/homebrew-core" = homebrew-core;
      "homebrew/homebrew-cask" = homebrew-cask;
    };
  };

  # Configure homebrew cask management
  homebrew = {
    enable = true;
    onActivation = {
      autoUpdate = false;
      cleanup = "none";
    };
    casks = [
      "linearmouse"
      "middleclick"
    ];
  };
}
