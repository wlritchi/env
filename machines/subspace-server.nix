# A local subspace server (https://github.com/wlritchi/subspace) as a user
# service: systemd on Linux, launchd on macOS. Off by default. A host module or
# the private overlay turns it on:
#
#   custom.subspace-server.enable = true;
#
# The server gets GITHUB_TOKEN from the gh login (githubTokenFromGh).
#
# The `subspace` plugin's relay needs no setup for this server. With no URL
# setting, it connects to ws://127.0.0.1:8137 with the server's token from
# <data dir>/token. It finds the data directory with the server's rule:
# SUBSPACE_DATA_DIR, else $XDG_DATA_HOME/subspace. If the relay has a URL (the
# plugin's server_url option or SUBSPACE_WS_URL), it does not read that file,
# so it also needs a credential (the token option or SUBSPACE_TOKEN).
{
  config,
  lib,
  pkgs,
  subspace,
  ...
}:

let
  cfg = config.custom.subspace-server;

  start = pkgs.writeShellApplication {
    name = "subspace-server-start";
    runtimeInputs = [
      cfg.package
      pkgs.coreutils
    ];
    text = builtins.readFile ./subspace-server-start.bash;
  };

  environment = {
    SUBSPACE_WS_HOST = cfg.host;
    SUBSPACE_WS_PORT = toString cfg.port;
    SUBSPACE_DATA_DIR = cfg.dataDir;
  }
  // lib.optionalAttrs (cfg.environmentFile != null) {
    WLR_SUBSPACE_ENV_FILE = cfg.environmentFile;
  }
  // lib.optionalAttrs cfg.githubTokenFromGh {
    WLR_SUBSPACE_GH = lib.getExe config.programs.gh.package;
  }
  // cfg.environment;

  logFile = "${config.home.homeDirectory}/Library/Logs/subspace-server.log";
in
{
  options.custom.subspace-server = {
    enable = lib.mkEnableOption "a local subspace server for Claude Code notifications";

    package = lib.mkOption {
      type = lib.types.package;
      default = pkgs.callPackage ./pkgs/subspace-server.nix { src = subspace; };
      defaultText = lib.literalExpression "the release pinned by the `subspace` flake input";
      description = "subspace server package.";
    };

    host = lib.mkOption {
      type = lib.types.str;
      default = "127.0.0.1";
      description = "WebSocket bind address.";
    };

    port = lib.mkOption {
      type = lib.types.port;
      default = 8137;
      description = ''
        WebSocket port. A relay with no URL setting connects to 8137. For a
        different port, give the relay the URL (the plugin's server_url
        option or SUBSPACE_WS_URL) and a credential (the token option or
        SUBSPACE_TOKEN).
      '';
    };

    dataDir = lib.mkOption {
      type = lib.types.str;
      default = "${config.xdg.dataHome}/subspace";
      defaultText = lib.literalExpression ''"''${config.xdg.dataHome}/subspace"'';
      description = ''
        Where the server keeps its state and its token. If this is not the
        default, set SUBSPACE_DATA_DIR to the same value for Claude Code, so
        that relays find the token.
      '';
    };

    environment = lib.mkOption {
      type = lib.types.attrsOf lib.types.str;
      default = { };
      example = {
        SUBSPACE_SESSION_TTL_DAYS = "14";
      };
      description = ''
        Other server settings (see server/README.md in the subspace
        repository). The values go into the Nix store, so put secrets in
        environmentFile.
      '';
    };

    environmentFile = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      example = lib.literalExpression ''"''${config.xdg.configHome}/subspace/env"'';
      description = ''
        File that bash sources with `set -a` before the server starts, for
        secrets such as a GITHUB_TOKEN other than the one from gh. Because
        bash runs it, lines can use command substitution; give commands by
        absolute path, because the service has a minimal PATH.
      '';
    };

    githubTokenFromGh = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = ''
        Set GITHUB_TOKEN (needed for PR monitoring) from `gh auth token` each
        time the server starts, unless environmentFile or environment already
        sets it. If gh has no token, the start fails and the service tries
        again. Set this to false on a host without a gh login.
      '';
    };
  };

  config = lib.mkIf cfg.enable {
    # The migration and credential scripts are also useful by hand.
    home.packages = [ cfg.package ];

    systemd.user.services.subspace-server = lib.mkIf pkgs.stdenv.hostPlatform.isLinux {
      Unit = {
        Description = "subspace server for Claude Code notifications";
        Documentation = "https://github.com/wlritchi/subspace/blob/main/server/README.md";
      };
      Service = {
        Type = "simple";
        ExecStart = lib.getExe start;
        Environment = lib.mapAttrsToList (name: value: "${name}=${value}") environment;
        UMask = "0077";
        Restart = "on-failure";
        RestartSec = 5;
      };
      Install.WantedBy = [ "default.target" ];
    };

    launchd.agents.subspace-server = lib.mkIf pkgs.stdenv.hostPlatform.isDarwin {
      enable = true;
      config = {
        ProgramArguments = [ (lib.getExe start) ];
        EnvironmentVariables = environment;
        Umask = 63;
        RunAtLoad = true;
        KeepAlive = true;
        ProcessType = "Background";
        ThrottleInterval = 5;
        StandardOutPath = logFile;
        StandardErrorPath = logFile;
      };
    };
  };
}
