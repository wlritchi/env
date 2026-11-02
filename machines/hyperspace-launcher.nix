# The Hyperspace launcher (https://github.com/wlritchi/hyperspace) as a user
# service: systemd on Linux, launchd on macOS. Off by default. A host module or
# the private overlay turns it on:
#
#   custom.hyperspace-launcher.enable = true;
#
# Without a service, the plugin's SessionStart hook starts the launcher, and
# the launcher stops after 10 minutes with no live session. `--stay` keeps it
# running, so that a subspace message to a stopped session resumes it.
#
# The hyperspace client is a uv tool, not a Nix package, and its server URL
# and token come from its own configuration (~/.config/hyperspace/config.toml
# and the keyring or token cache).
{
  config,
  lib,
  pkgs,
  ...
}:

let
  cfg = config.custom.hyperspace-launcher;

  start = pkgs.writeShellApplication {
    name = "hyperspace-launcher-start";
    text = builtins.readFile ./hyperspace-launcher-start.bash;
  };

  environment = {
    WLR_HYPERSPACE = cfg.executable;
  }
  // lib.optionalAttrs (cfg.claudeArgs != null) {
    # systemd removes the quotes from an Environment= value, so the JSON goes
    # in a file.
    WLR_HYPERSPACE_CLAUDE_ARGS_FILE = toString (
      pkgs.writeText "hyperspace-launcher-claude-args.json" (builtins.toJSON cfg.claudeArgs)
    );
  }
  // lib.optionalAttrs (cfg.environmentFile != null) {
    WLR_HYPERSPACE_ENV_FILE = cfg.environmentFile;
  }
  // cfg.environment;

  logFile = "${config.home.homeDirectory}/Library/Logs/hyperspace-launcher.log";
in
{
  options.custom.hyperspace-launcher = {
    enable = lib.mkEnableOption "a Hyperspace launcher that runs all the time";

    executable = lib.mkOption {
      type = lib.types.str;
      default = "${config.home.homeDirectory}/.local/bin/hyperspace";
      defaultText = lib.literalExpression ''"''${config.home.homeDirectory}/.local/bin/hyperspace"'';
      description = "The hyperspace client, as `uv tool install` puts it.";
    };

    claudeArgs = lib.mkOption {
      type = lib.types.nullOr (lib.types.listOf lib.types.str);
      default = [
        "--dangerously-load-development-channels"
        "plugin:subspace@subspace"
        # The launcher puts its first message after these options, so the
        # last option cannot take a list of values. The settings already
        # give this mode, so this option changes nothing.
        "--permission-mode"
        "auto"
      ];
      description = ''
        Claude Code options for each session that the launcher resumes
        (HYPERSPACE_LAUNCHER_CLAUDE_ARGS). They replace launcher_claude_args
        in config.toml; null uses config.toml. The resumed session gets
        subspace messages only if the subspace channel loads. The launcher
        refuses some options, and the last option must not take a list of
        values (see docs/runners.md in the hyperspace repository).
      '';
    };

    environment = lib.mkOption {
      type = lib.types.attrsOf lib.types.str;
      default = { };
      example = {
        HYPERSPACE_HOST = "workstation";
      };
      description = ''
        Other client settings. The values go into the Nix store, so put
        secrets in environmentFile.
      '';
    };

    environmentFile = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      example = lib.literalExpression ''"''${config.xdg.configHome}/hyperspace/launcher.env"'';
      description = ''
        File that bash sources with `set -a` before the launcher starts.
        Because bash runs it, lines can use command substitution. The
        sessions that the launcher resumes get this environment. For values
        that only the resumes need, such as NOTIFICATIONS_TOKEN, prefer
        launcher_env and launcher_secret_env in config.toml (client 0.7.0
        and later), which keep the secrets in the keyring.
      '';
    };
  };

  config = lib.mkIf cfg.enable {
    systemd.user.services.hyperspace-launcher = lib.mkIf pkgs.stdenv.hostPlatform.isLinux {
      Unit = {
        Description = "Hyperspace launcher";
        Documentation = "https://github.com/wlritchi/hyperspace/blob/main/docs/deploy.md";
      };
      Service = {
        Type = "simple";
        ExecStart = lib.getExe start;
        Environment = lib.mapAttrsToList (name: value: "${name}=${value}") environment;
        # `claude --bg` can start the Claude Code daemon as a child of the
        # launcher. Stop only the launcher, so that a restart does not stop
        # the background sessions.
        KillMode = "process";
        # With no token in the cache yet, the launcher stops; try again later.
        Restart = "always";
        RestartSec = 60;
      };
      Install.WantedBy = [ "default.target" ];
    };

    launchd.agents.hyperspace-launcher = lib.mkIf pkgs.stdenv.hostPlatform.isDarwin {
      enable = true;
      config = {
        ProgramArguments = [ (lib.getExe start) ];
        EnvironmentVariables = environment;
        RunAtLoad = true;
        KeepAlive = true;
        # See KillMode above.
        AbandonProcessGroup = true;
        ProcessType = "Background";
        ThrottleInterval = 60;
        StandardOutPath = logFile;
        StandardErrorPath = logFile;
      };
    };
  };
}
