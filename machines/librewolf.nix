{
  config,
  pkgs,
  lib,
  ...
}:

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

  # The old sidebar (sidebar.revamp = false) puts a title and close button
  # above Sidebery, and Nova (browser.nova.enabled) draws a border with a
  # rounded corner around the panel. Show only the extension.
  sidebarCss = pkgs.writeText "sidebar.css" ''
    #sidebar-box[sidebarcommand="_3c078156-979c-498b-8990-85f7987dd929_-sidebar-action"] #sidebar-header {
      display: none !important;
    }
    #sidebar-box,
    #sidebar-box #sidebar {
      border: none !important;
      border-radius: 0 !important;
    }
  '';

  # CustomizableUI.makeWidgetId: toolbar widget ids are the extension id
  # lowercased with everything outside [a-z0-9_-] replaced by "_"
  widgetId =
    id:
    "${
      lib.stringAsChars (c: if builtins.match "[a-z0-9_-]" c != null then c else "_") (lib.toLower id)
    }-browser-action";

  # Reapplied from user.js on every launch, so manual toolbar rearrangement
  # reverts on restart; edit here instead.
  uiCustomizationState = builtins.toJSON {
    placements = {
      "widget-overflow-fixed-list" = [ ];
      "unified-extensions-area" = [ ];
      "nav-bar" =
        map (e: widgetId e.id) cfg.extensions.left
        ++ [
          "back-button"
          "forward-button"
          "vertical-spacer"
          "stop-reload-button"
          "urlbar-container"
        ]
        ++ map (e: widgetId e.id) cfg.extensions.right
        ++ [
          "unified-extensions-button"
          "downloads-button"
        ];
      "toolbar-menubar" = [ "menubar-items" ];
      "TabsToolbar" = [
        "tabbrowser-tabs"
        "new-tab-button"
        "spring"
        "alltabs-button"
      ];
      "PersonalToolbar" = [ "personal-bookmarks" ];
    };
    currentVersion = 26;
  };

  cfg = config.custom.librewolf;
  librewolf = config.programs.librewolf;

  extensionType = lib.types.submodule {
    options = {
      id = lib.mkOption {
        type = lib.types.str;
        description = "Extension ID.";
      };
      slug = lib.mkOption {
        type = lib.types.nullOr lib.types.str;
        default = null;
        description = "AMO slug. The extension installs and updates from AMO.";
      };
      xpi = lib.mkOption {
        type = lib.types.nullOr lib.types.package;
        default = null;
        description = "Pinned XPI. The extension installs from this file and does not update itself.";
      };
    };
  };

  extensionList =
    description:
    lib.mkOption {
      type = lib.types.listOf extensionType;
      default = [ ];
      inherit description;
    };

  extensionPolicy =
    e:
    if e.xpi != null then
      {
        installation_mode = "force_installed";
        install_url = "file://${e.xpi}";
        updates_disabled = true;
      }
    else
      {
        installation_mode = "force_installed";
        install_url = "https://addons.mozilla.org/firefox/downloads/latest/${e.slug}/latest.xpi";
      };

  # Install hash that LibreWolf derives from its install directory, here the
  # Arch package's /usr/lib/librewolf
  linuxInstallHash = "6C1CE26D3274EA5B";
in
{
  options.custom.librewolf = {
    enable = lib.mkOption {
      type = lib.types.bool;
      default = pkgs.stdenv.isDarwin;
      description = ''
        Whether to manage LibreWolf and its default profile. On Linux, a host
        enables this after it adopts its existing profile; see
        profiles.default below.
      '';
    };

    # Toolbar button order follows list order. Other modules (such as the
    # private overlay) append to these lists.
    extensions = {
      left = extensionList "Force-installed extensions pinned before the back button.";
      right = extensionList "Force-installed extensions pinned after the urlbar.";
      unpinned = extensionList "Force-installed extensions in the extensions menu only.";
    };
  };

  config = lib.mkIf cfg.enable {
    custom.librewolf.extensions = {
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
        {
          # Web Archives
          id = "{d07ccf11-c0cd-4938-a265-2a4d6ad01189}";
          slug = "view-page-archive";
        }
        {
          id = "gdpr@cavi.au.dk";
          slug = "consent-o-matic";
        }
        {
          # Bypass Paywalls Clean is not on AMO. Its gitflic.ru update manifest
          # has no hashes, and LibreWolf does not require signed extensions,
          # so pin the XPI by hash and turn off its updates. To update, change
          # the version and hash.
          id = "magnolia@12.34";
          xpi = pkgs.fetchurl {
            name = "bypass_paywalls_clean-4.4.7.2.xpi";
            url = "https://gitflic.ru/project/magnolia1234/bpc_uploads/blob/raw?file=bypass_paywalls_clean-4.4.7.2.xpi";
            hash = "sha256-yj2Tip3vTy/rDd0m8SK3cB4i2kWHGhg+KyygIZZp2z4=";
          };
        }
      ];
    };

    programs.librewolf = lib.mkMerge [
      {
        enable = true;

        # Baked into the nixpkgs wrapper's distribution/policies.json. With a
        # distro-packaged LibreWolf, librewolf-extension.nix writes the
        # policies to ~/.librewolf/policies/policies.json instead.
        policies.ExtensionSettings = lib.listToAttrs (
          map (e: lib.nameValuePair e.id (extensionPolicy e)) (
            cfg.extensions.left ++ cfg.extensions.right ++ cfg.extensions.unpinned
          )
        );

        # To adopt a pre-existing profile (keeping its data), run
        # wlr-librewolf-adopt-profile before the first rebuild; it moves the
        # profile to this path and clears the stale ini files. Alternatively,
        # set profiles.default.path to the existing directory name in the host
        # module, and move profiles.ini and chrome/userChrome.css aside.
        profiles.default = {
          isDefault = true;

          settings = {
            # LibreWolf defaults this to true ("Clear history when LibreWolf closes")
            "privacy.sanitize.sanitizeOnShutdown" = false;
            "toolkit.legacyUserProfileCustomizations.stylesheets" = true;
            # The revamped sidebar puts extension panels such as Sidebery in an
            # inset card. LibreWolf 157 resets this pref once to roll it out.
            "sidebar.revamp" = false;
            "browser.uiCustomization.state" = uiCustomizationState;
            "browser.startup.page" = 3; # restore the previous session
            "browser.sessionstore.restore_pinned_tabs_on_demand" = true;
            "browser.toolbars.bookmarks.visibility" = "never";
            "view_source.wrap_long_lines" = true;
            # Strict tracking protection without the site-compatibility exceptions
            "privacy.trackingprotection.allow_list.baseline.enabled" = false;
          };

          search = {
            force = true; # replace any existing search.json.mozlz4
            default = "kagi";
            privateDefault = "kagi";
            engines = {
              kagi = {
                name = "Kagi";
                urls = [ { template = "https://kagi.com/search?q={searchTerms}"; } ];
              };
              youtube = {
                name = "YouTube";
                urls = [ { template = "https://www.youtube.com/results?search_query={searchTerms}"; } ];
                definedAliases = [ "yt" ];
              };
              wikipedia.metaData.alias = "wp";
              bing.metaData.hidden = true;
              ddg.metaData.hidden = true;
              google.metaData.hidden = true;
              mojeek.metaData.hidden = true;
              perplexity.metaData.hidden = true;
              startpage.metaData.hidden = true;
            };
          };

          # Concatenated at build time (not builtins.readFile) so cross-platform
          # QA evals don't require building the fetcher for the foreign system
          userChrome = pkgs.concatText "userChrome.css" (
            [
              "${firefox-csshacks}/chrome/window_control_placeholder_support.css"
              "${firefox-csshacks}/chrome/${hideTabsCss}"
              sidebarCss
            ]
            ++ lib.optional pkgs.stdenv.isDarwin macosWindowControlSpace
          );
        };
      }

      (lib.mkIf pkgs.stdenv.isDarwin {
        # The nixpkgs wrapper makes the bundle executable a shell script that execs
        # .librewolf-old. On macOS 27, LaunchServices reports pid -1 for apps that
        # start this way, and AeroSpace cannot manage them. Point the bundle at the
        # real binary and move the wrapper's MOZ_* variables into LSEnvironment.
        # LD_LIBRARY_PATH and PATH are not copied: dyld ignores LD_LIBRARY_PATH.
        package = pkgs.librewolf.overrideAttrs (old: {
          buildCommand = old.buildCommand + ''
            contents="$out/Applications/LibreWolf.app/Contents"
            cp --remove-destination "$(readlink -f "$contents/Info.plist")" "$contents/Info.plist"
            chmod u+w "$contents/Info.plist"
            ${pkgs.python3.interpreter} - "$contents" <<'EOF'
            import plistlib, re, sys
            contents = sys.argv[1]
            with open(f"{contents}/Info.plist", "rb") as f:
                info = plistlib.load(f)
            with open(f"{contents}/MacOS/{info['CFBundleExecutable']}") as f:
                wrapper = f.read()
            env = dict(re.findall(r"^export (MOZ_\w+)='([^']*)'$", wrapper, re.M))
            real = re.search(r'^exec "([^"]+)"', wrapper, re.M).group(1)
            info["CFBundleExecutable"] = real.rsplit("/", 1)[1]
            info["LSEnvironment"] = env
            with open(f"{contents}/Info.plist", "wb") as f:
                plistlib.dump(info, f)
            EOF
          '';
        });
      })

      # Use the distro package; manage only the profile
      (lib.mkIf pkgs.stdenv.isLinux { package = null; })
    ];

    # The nixpkgs wrapper sets MOZ_LEGACY_PROFILES=1, but the distro package
    # does not. Without that variable, LibreWolf reads its install's default
    # profile only from an [Install...] section in profiles.ini. If the section
    # is missing, LibreWolf creates a new empty profile and tries to write it
    # to the read-only profiles.ini.
    home.file.".librewolf/profiles.ini" = lib.mkIf pkgs.stdenv.isLinux {
      text = lib.mkForce (
        lib.generators.toINI { } {
          General = {
            StartWithLastProfile = 1;
            Version = 2;
          };
          Profile0 = {
            Name = librewolf.profiles.default.name;
            Path = librewolf.profiles.default.path;
            IsRelative = 1;
            Default = 1;
          };
          "Install${linuxInstallHash}" = {
            Default = librewolf.profiles.default.path;
            Locked = 1;
          };
        }
      );
    };
  };
}
