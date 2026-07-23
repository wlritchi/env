{ pkgs, lib, ... }:

let
  # Pinned to the same rev as the checkout in the Linux profile's chrome/ dir
  firefox-csshacks = pkgs.fetchFromGitHub {
    owner = "MrOtherGuy";
    repo = "firefox-csshacks";
    rev = "b8e0cb847e60087dc7cb831d778bcc05099cd36a";
    hash = "sha256-iTMRvEhkQ+uj+UywSLnKLS2pg5AEN3tyog3f1Tc+rtk=";
  };

  hideTabsCss = if pkgs.stdenv.isDarwin then "hide_tabs_toolbar_osx.css" else "hide_tabs_toolbar.css";

  # Firefox 134+ renamed the :root attribute tabsintitlebar -> customtitlebar,
  # so the pinned window_control_placeholder_support.css rules never match and
  # the macOS traffic-light buttons overlap the leftmost nav-bar widgets.
  # Upstream deprecated the style rather than fixing it, so reserve the space
  # ourselves (window controls sit on the LEFT on macOS).
  macosWindowControlSpace = pkgs.writeText "macos-window-control-space.css" ''
    @media (-moz-platform: macos) {
      :root:is([customtitlebar], [tabsintitlebar]) {
        --uc-window-control-width: 72px;
        --uc-window-drag-space-pre: 30px;
        --uc-window-drag-space-post: 30px;
      }
      :root:is([customtitlebar], [tabsintitlebar])[sizemode="fullscreen"] {
        --uc-window-control-width: 0px;
      }
      :root:is([customtitlebar], [tabsintitlebar])[sizemode="maximized"] {
        --uc-window-drag-space-pre: 0px;
      }
      :root:is([customtitlebar], [tabsintitlebar]) #nav-bar {
        border-inline: 0px solid transparent;
        border-inline-style: solid !important;
        border-inline-width: calc(
            var(--uc-window-control-width, 0px) + var(--uc-window-drag-space-pre, 0px)
          )
          var(--uc-window-drag-space-post, 0px);
        background-clip: border-box !important;
      }
    }
  '';

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

    # To adopt a pre-existing profile (keeping its data), run
    # wlr-librewolf-adopt-profile before the first rebuild; it moves the
    # profile to this path and clears the stale ini files.
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
      userChrome = pkgs.concatText "userChrome.css" (
        [
          "${firefox-csshacks}/chrome/window_control_placeholder_support.css"
          "${firefox-csshacks}/chrome/${hideTabsCss}"
        ]
        ++ lib.optional pkgs.stdenv.isDarwin macosWindowControlSpace
      );
    };
  };
}
