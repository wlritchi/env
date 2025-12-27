{
  config,
  pkgs,
  lib,
  ...
}:

let
  # Build the LibreWolf workspace tracker extension
  librewolf-workspace-tracker = pkgs.stdenv.mkDerivation {
    pname = "librewolf-workspace-tracker";
    version = "0.1.0";

    src = ../src/librewolf-workspace-tracker;

    nativeBuildInputs = [ pkgs.zip ];

    buildPhase = ''
      # Create XPI (unsigned extension package)
      zip -r librewolf-workspace-tracker.xpi \
        manifest.json \
        background.js \
        native-messaging.js \
        restoration.js \
        icons/
    '';

    installPhase = ''
      mkdir -p $out
      cp librewolf-workspace-tracker.xpi $out/
      cp manifest.json $out/
    '';

    meta = {
      description = "Track LibreWolf windows across niri workspaces";
      homepage = "https://github.com/wlritchi/wlrenv";
    };
  };

  # Native messaging manifest
  nativeMessagingManifest = pkgs.writeTextFile {
    name = "wlr-librewolf-workspace-tracker-manifest";
    destination = "/wlr_librewolf_workspace_tracker.json";
    text = builtins.toJSON {
      name = "wlr_librewolf_workspace_tracker";
      description = "niri workspace tracking for LibreWolf";
      path = "${config.home.homeDirectory}/.wlrenv/bin/wayland/wlr-librewolf-native-host";
      type = "stdio";
      allowed_extensions = [ "librewolf-workspace-tracker@wlrenv" ];
    };
  };

  # LibreWolf policies for automatic extension installation
  librewolfPolicies = pkgs.writeTextFile {
    name = "librewolf-policies";
    destination = "/policies.json";
    text = builtins.toJSON {
      policies = {
        # Allow installation of unsigned extensions
        ExtensionSettings = {
          "librewolf-workspace-tracker@wlrenv" = {
            installation_mode = "force_installed";
            install_url = "file://${config.home.homeDirectory}/.wlrenv/build/librewolf-workspace-tracker/librewolf-workspace-tracker.xpi";
          };
        };
      };
    };
  };

in
{
  # Install the extension XPI to the wlrenv build directory
  home.file.".wlrenv/build/librewolf-workspace-tracker/librewolf-workspace-tracker.xpi".source =
    "${librewolf-workspace-tracker}/librewolf-workspace-tracker.xpi";

  # Install LibreWolf policies for automatic extension installation
  home.file.".librewolf/policies/policies.json".source = "${librewolfPolicies}/policies.json";

  # Install native messaging manifest
  # LibreWolf looks for native messaging hosts in ~/.librewolf/native-messaging-hosts/
  home.file.".librewolf/native-messaging-hosts/wlr_librewolf_workspace_tracker.json".source =
    "${nativeMessagingManifest}/wlr_librewolf_workspace_tracker.json";

  # Ensure the native host script is executable (already in repo, just verify)
  # The script is already part of the wlrenv repo at bin/wayland/wlr-librewolf-native-host
  # and is made executable in the repo

  # Create state directory
  home.activation.createLibrewolfStateDir = lib.hm.dag.entryAfter [ "writeBoundary" ] ''
    $DRY_RUN_CMD mkdir -p ${config.home.homeDirectory}/.local/state/librewolf
  '';
}
