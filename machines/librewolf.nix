{ pkgs, ... }:

let
  # Pinned to the same rev as the checkout in the Linux profile's chrome/ dir
  firefox-csshacks = pkgs.fetchFromGitHub {
    owner = "MrOtherGuy";
    repo = "firefox-csshacks";
    rev = "b8e0cb847e60087dc7cb831d778bcc05099cd36a";
    hash = "sha256-iTMRvEhkQ+uj+UywSLnKLS2pg5AEN3tyog3f1Tc+rtk=";
  };

  hideTabsCss = if pkgs.stdenv.isDarwin then "hide_tabs_toolbar_osx.css" else "hide_tabs_toolbar.css";

  sideberyWidget = "_3c078156-979c-498b-8990-85f7987dd929_-browser-action";
  darkReaderWidget = "addon_darkreader_org-browser-action";

  # Reapplied from user.js on every launch, so manual toolbar rearrangement
  # reverts on restart; edit here instead.
  uiCustomizationState = builtins.toJSON {
    placements = {
      "widget-overflow-fixed-list" = [ ];
      "unified-extensions-area" = [ ];
      "nav-bar" = [
        sideberyWidget
        "back-button"
        "forward-button"
        "vertical-spacer"
        "stop-reload-button"
        "urlbar-container"
        darkReaderWidget
        "unified-extensions-button"
        "downloads-button"
      ];
      "toolbar-menubar" = [ "menubar-items" ];
      "TabsToolbar" = [
        "tabbrowser-tabs"
        "new-tab-button"
        "alltabs-button"
      ];
      "PersonalToolbar" = [ "personal-bookmarks" ];
    };
    currentVersion = 24;
  };
in
{
  programs.librewolf = {
    enable = true;

    # Baked into the nixpkgs wrapper's distribution/policies.json, so this
    # works on both darwin and Linux as long as the package comes from nixpkgs
    # (Linux machines using a distro-packaged LibreWolf need
    # ~/.librewolf/policies/policies.json instead; see librewolf-extension.nix).
    policies = {
      ExtensionSettings = {
        "{3c078156-979c-498b-8990-85f7987dd929}" = {
          installation_mode = "force_installed";
          install_url = "https://addons.mozilla.org/firefox/downloads/latest/sidebery/latest.xpi";
        };
        "addon@darkreader.org" = {
          installation_mode = "force_installed";
          install_url = "https://addons.mozilla.org/firefox/downloads/latest/darkreader/latest.xpi";
        };
      };
    };

    # To adopt a pre-existing profile, set `path` to its directory name under
    # Profiles/ (e.g. "abcd1234.default") and remove any stale installs.ini
    # whose locked default points elsewhere.
    profiles.default = {
      isDefault = true;

      settings = {
        # LibreWolf defaults this to true ("Clear history when LibreWolf closes")
        "privacy.sanitize.sanitizeOnShutdown" = false;
        "toolkit.legacyUserProfileCustomizations.stylesheets" = true;
        "browser.uiCustomization.state" = uiCustomizationState;
      };

      # Concatenated at build time (not builtins.readFile) so cross-platform
      # QA evals don't require building the fetcher for the foreign system
      userChrome = pkgs.concatText "userChrome.css" [
        "${firefox-csshacks}/chrome/window_control_placeholder_support.css"
        "${firefox-csshacks}/chrome/${hideTabsCss}"
      ];
    };
  };
}
