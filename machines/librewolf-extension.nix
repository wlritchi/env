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
      path = "${config.home.homeDirectory}/.local/bin/wlr-niri-librewolf-host";
      type = "stdio";
      allowed_extensions = [ "librewolf-workspace-tracker@wlrenv" ];
    };
  };

  extensionSettings = {
    "librewolf-workspace-tracker@wlrenv" = {
      installation_mode = "force_installed";
      install_url = "file://${config.home.homeDirectory}/.wlrenv/build/librewolf-workspace-tracker/librewolf-workspace-tracker.xpi";
    };
  };

  # LibreWolf policies for automatic extension installation
  librewolfPolicies = pkgs.writeTextFile {
    name = "librewolf-policies";
    destination = "/policies.json";
    text = builtins.toJSON {
      policies = {
        ExtensionSettings = extensionSettings;
      };
    };
  };

in
{
  # Install the extension XPI to the wlrenv build directory
  home.file.".wlrenv/build/librewolf-workspace-tracker/librewolf-workspace-tracker.xpi".source =
    "${librewolf-workspace-tracker}/librewolf-workspace-tracker.xpi";

  # Install LibreWolf policies for automatic extension installation. Firefox's
  # policy engine does not merge ExtensionSettings across sources, so when
  # programs.librewolf manages the browser (librewolf.nix), the entry must go
  # through its policies option (merged into the wrapper's policies.json) and
  # the user-level file must be absent. The user-level file is only for a
  # distro-packaged LibreWolf.
  programs.librewolf.policies.ExtensionSettings = extensionSettings;
  home.file.".librewolf/policies/policies.json" = lib.mkIf (!config.programs.librewolf.enable) {
    source = "${librewolfPolicies}/policies.json";
  };

  # Install native messaging manifest
  # LibreWolf looks for native messaging hosts in ~/.librewolf/native-messaging-hosts/
  home.file.".librewolf/native-messaging-hosts/wlr_librewolf_workspace_tracker.json".source =
    "${nativeMessagingManifest}/wlr_librewolf_workspace_tracker.json";
}
