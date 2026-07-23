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

  # CustomizableUI.makeWidgetId: toolbar widget ids are the extension id
  # lowercased with everything outside [a-z0-9_-] replaced by "_"
  widgetId =
    id:
    "${
      lib.stringAsChars (c: if builtins.match "[a-z0-9_-]" c != null then c else "_") (lib.toLower id)
    }-browser-action";

  # Force-installed AMO extensions. Toolbar button order follows list order:
  # `left` entries sit before the back button, `right` entries after the
  # urlbar, and `unpinned` entries land in the extensions overflow menu.
  extensions = {
    left = [
      {
        id = "{3c078156-979c-498b-8990-85f7987dd929}";
        slug = "sidebery";
      }
      {
        id = "tab-counter@daawesomep.addons.mozilla.org";
        slug = "tab-counter-webext";
      }
    ];
    right = [
      {
        id = "addon@darkreader.org";
        slug = "darkreader";
      }
      {
        # Extension counterpart to programs.browserpass, which only installs
        # the native messaging host
        id = "browserpass@maximbaz.com";
        slug = "browserpass-ce";
      }
      {
        id = "uBlock0@raymondhill.net";
        slug = "ublock-origin";
      }
      {
        id = "{531906d3-e22f-4a6c-a102-8057b88a1a63}";
        slug = "single-file";
      }
      {
        id = "sponsorBlocker@ajay.app";
        slug = "sponsorblock";
      }
      {
        id = "{55f61747-c3d3-4425-97f9-dfc19a0be23c}";
        slug = "spoof-timezone";
      }
      {
        # Stylus
        id = "{7a7a4a92-a2a0-41d1-9fd7-1e92480d612d}";
        slug = "styl-us";
      }
      {
        id = "{aecec67f-0d10-4fa7-b7c7-609a2db280cf}";
        slug = "violentmonkey";
      }
      {
        # Web Archives
        id = "{d07ccf11-c0cd-4938-a265-2a4d6ad01189}";
        slug = "view-page-archive";
      }
    ];
    unpinned = [
      {
        id = "{a4c4eda4-fb84-4a84-b4a1-f7c1cbf2a1ad}";
        slug = "refined-github-";
      }
      {
        id = "@react-devtools";
        slug = "react-devtools";
      }
      {
        # I still don't care about cookies
        id = "idcac-pub@guus.ninja";
        slug = "istilldontcareaboutcookies";
      }
      {
        id = "tabclosegold@mukunku.com";
        slug = "tab-close-gold";
      }
      {
        # Duplicate Tabs Closer
        id = "jid0-RvYT2rGWfM8q5yWxIxAHYAeo5Qg@jetpack";
        slug = "duplicate-tabs-closer";
      }
      {
        id = "search@kagi.com";
        slug = "kagi-search-for-firefox";
      }
    ];
  };

  # Reapplied from user.js on every launch, so manual toolbar rearrangement
  # reverts on restart; edit here instead.
  uiCustomizationState = builtins.toJSON {
    placements = {
      "widget-overflow-fixed-list" = [ ];
      "unified-extensions-area" = [ ];
      "nav-bar" =
        map (e: widgetId e.id) extensions.left
        ++ [
          "back-button"
          "forward-button"
          "vertical-spacer"
          "stop-reload-button"
          "urlbar-container"
        ]
        ++ map (e: widgetId e.id) extensions.right
        ++ [
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
      ExtensionSettings = lib.listToAttrs (
        map (
          e:
          lib.nameValuePair e.id {
            installation_mode = "force_installed";
            install_url = "https://addons.mozilla.org/firefox/downloads/latest/${e.slug}/latest.xpi";
          }
        ) (extensions.left ++ extensions.right ++ extensions.unpinned)
      );
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

      search = {
        force = true; # replace any existing search.json.mozlz4
        default = "kagi";
        engines.kagi = {
          name = "Kagi";
          urls = [ { template = "https://kagi.com/search?q={searchTerms}"; } ];
          definedAliases = [ "@kagi" ];
        };
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
