{
  lib,
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
  security.pam.services.sudo_local = {
    touchIdAuth = true;
    reattach = true; # make TouchID work inside tmux
  };

  # Set the primary user
  system.primaryUser = username;

  # System preferences
  system.defaults.NSGlobalDomain = {
    ApplePressAndHoldEnabled = false; # Disable press-and-hold for accent menu, enable key repeat instead
    KeyRepeat = 2; # fastest
    InitialKeyRepeat = 35; # 4th of 6 ticks on Delay until repeat
    "com.apple.swipescrolldirection" = false; # traditional scrolling
  };

  system.defaults.dock = {
    orientation = "right";
    tilesize = 16;
    autohide = true;
    autohide-delay = 3.0;
  };

  # Disable the four Window > Move & Resize half-tile shortcuts. The Keyboard
  # Shortcuts UI labels them Tile {Left,Right,Top,Bottom} Half with Ctrl+Fn+arrow
  # equivalents, but on external keyboards Fn+arrow is just how the internal
  # layer expresses Home/End/PageUp/PageDown, so the bindings actually fire on
  # Ctrl+{Home,End,PageUp,PageDown}. The menu items themselves are simply named
  # Left/Right/Top/Bottom. Remap to an unreachable combo (cmd+opt+ctrl+shift+DEL)
  # so the menu items still work via click. Window > Minimize (cmd+m) is also
  # neutered here — too easy to hit by accident and recovering a minimized
  # window without the dock is a hassle.
  system.defaults.CustomUserPreferences.NSGlobalDomain.NSUserKeyEquivalents = {
    "Left" = "@~^$\\U007F";
    "Right" = "@~^$\\U007F";
    "Top" = "@~^$\\U007F";
    "Bottom" = "@~^$\\U007F";
    "Minimize" = "@~^$\\U007F";
  };

  system.defaults.CustomUserPreferences."com.apple.symbolichotkeys".AppleSymbolicHotKeys = {
    "64" = {
      # Show Spotlight search - alt+p
      enabled = true;
      value = {
        type = "standard";
        parameters = [
          112
          35
          524288
        ];
      };
    };
    "53" = {
      # decrease brightness (F14)
      enabled = false;
    };
    "54" = {
      # decrease brightness (F15)
      enabled = false;
    };
    "55" = {
      # open display settings?? (alt+F14)
      enabled = false;
    };
    "56" = {
      # open display settings?? (alt+F15)
      enabled = false;
    };
  };

  # Register podvorak (custom Dvorak-based layout) as an enabled input source.
  # The KeyboardLayout ID is the `id` attribute from podvorak.keylayout, and the
  # name must match the layout's `name` attribute. The .keylayout file itself is
  # copied into ~/Library/Keyboard Layouts/ by home-manager (see darwin.nix).
  system.defaults.CustomUserPreferences."com.apple.HIToolbox".AppleEnabledInputSources = [
    {
      InputSourceKind = "Keyboard Layout";
      "KeyboardLayout ID" = -27322;
      "KeyboardLayout Name" = "Programisto Dvorak";
    }
    {
      InputSourceKind = "Keyboard Layout";
      "KeyboardLayout ID" = 29;
      "KeyboardLayout Name" = "Canadian";
    }
    {
      "Bundle ID" = "com.apple.CharacterPaletteIM";
      InputSourceKind = "Non Keyboard Input Method";
    }
  ];

  # autokbisw is a Swift CLI binary launched from aerospace startup; without a
  # bundle identifier its UserDefaults.standard writes to the `autokbisw` domain
  # (~/Library/Preferences/autokbisw.plist). Seed per-keyboard layout mappings
  # so a fresh install picks the right layout immediately instead of requiring
  # a manual selection on each device.
  system.defaults.CustomUserPreferences."autokbisw" = {
    keyboardISMapping = {
      "Apple Internal Keyboard / Trackpad-[unknown-unknown-Apple-unknown]" =
        "org.unknown.keylayout.ProgramistoDvorak";
      "Moonlander Mark I-[12951-6505-ZSA Technology Labs-default/latest]" =
        "com.apple.keylayout.Canadian";
      "Moonlander Mark I-[12951-6514-ZSA Technology Labs-default/latest]" =
        "com.apple.keylayout.Canadian";
    };
    mappingEnabled = {
      "Apple Internal Keyboard / Trackpad-[unknown-unknown-Apple-unknown]" = true;
      "Moonlander Mark I-[12951-6505-ZSA Technology Labs-default/latest]" = true;
      "Moonlander Mark I-[12951-6514-ZSA Technology Labs-default/latest]" = true;
    };
  };

  # cfprefsd caches user defaults in memory and will clobber on-disk writes from
  # `defaults write` (which is how CustomUserPreferences are applied). Restarting
  # it forces a re-read from disk so the HIToolbox / symbolichotkeys changes
  # actually stick without requiring a logout. Activation runs as root, so we
  # drop into the primary user's bootstrap namespace via `launchctl asuser` to
  # target the per-user cfprefsd (this is the same pattern nix-darwin itself
  # uses for user-scoped activation commands).
  system.activationScripts.postActivation.text =
    let
      user = lib.escapeShellArg username;
    in
    ''
      if uid=$(id -u -- ${user} 2>/dev/null) && launchctl asuser "$uid" true 2>/dev/null; then
        launchctl asuser "$uid" sudo --user=${user} -- killall cfprefsd 2>/dev/null || true
      fi
    '';

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
    # Prefer nixpkgs; brews are for tools not packaged there (displayplacer is
    # Homebrew-only as of 2026-07)
    brews = [
      "displayplacer"
    ];
    casks = [
      "jordanbaird-ice"
      "linearmouse"
      "middleclick"
      "swiftbar"
    ];
  };
}
