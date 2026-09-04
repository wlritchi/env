{
  config,
  pkgs,
  lib,
  ...
}:

let
  cc-openai-proxy = pkgs.callPackage ./pkgs/cc-openai-proxy.nix { };
in
{
  launchd.agents = {
    cc-openai-proxy = {
      enable = true;
      config = {
        ProgramArguments = [
          "${cc-openai-proxy}/bin/cc-openai-proxy"
          "--host"
          "127.0.0.1"
          "--port"
          "17780"
        ];
        # Anonymous access is temporary and limited to this loopback listener.
        EnvironmentVariables.CC_OPENAI_PROXY_ALLOW_ANON = "1";
        RunAtLoad = true;
        KeepAlive = true;
        ProcessType = "Background";
        ThrottleInterval = 1;
        StandardOutPath = "${config.home.homeDirectory}/Library/Logs/cc-openai-proxy.log";
        StandardErrorPath = "${config.home.homeDirectory}/Library/Logs/cc-openai-proxy.log";
      };
    };

    # Git sync - runs every hour
    git-sync = {
      enable = true;
      config = {
        ProgramArguments = [
          "${pkgs.bash}/bin/bash"
          "-c"
          ''
            git_sync_list="${config.xdg.configHome}/git-sync.list"
            if [ -f "$git_sync_list" ]; then
              ecode=0
              shuf "$git_sync_list" | while read d; do
                echo "Syncing $d"
                ${pkgs.git}/bin/git -C "$d" sync
                ecode=$?
                [ $ecode -ne 0 ] && break
              done
              exit $ecode
            fi
          ''
        ];
        RunAtLoad = true;
        StartInterval = 3600; # Run every hour
        StandardOutPath = "${config.home.homeDirectory}/Library/Logs/git-sync.log";
        StandardErrorPath = "${config.home.homeDirectory}/Library/Logs/git-sync.log";
      };
    };

    # Tmux pane backup - runs every 6 minutes
    tmux-pane-backup = {
      enable = true;
      config = {
        ProgramArguments = [
          "${pkgs.bash}/bin/bash"
          "${config.home.homeDirectory}/.wlrenv/bin/tmux/wlr-tmux-pane-backup"
        ];
        RunAtLoad = true;
        StartInterval = 360; # Run every 6 minutes
        StandardOutPath = "${config.home.homeDirectory}/Library/Logs/tmux-pane-backup.log";
        StandardErrorPath = "${config.home.homeDirectory}/Library/Logs/tmux-pane-backup.log";
      };
    };

    # Retainer cleanup for tmux pane backups - runs every hour
    retainer-tmux-panes = {
      enable = true;
      config = {
        ProgramArguments = [
          "${config.home.homeDirectory}/.local/bin/retainer"
          "-d"
          "${config.home.homeDirectory}/.local/share/tmux/resurrect"
          "-p"
          "pane_contents.(?P<timestamp>\\d{4}-\\d{2}-\\d{2}T\\d{6})\\.tar\\.gz"
          "-f"
          "%Y-%m-%dT%H%M%S"
        ];
        RunAtLoad = true;
        StartInterval = 3600;
        StandardOutPath = "${config.home.homeDirectory}/Library/Logs/retainer-tmux-panes.log";
        StandardErrorPath = "${config.home.homeDirectory}/Library/Logs/retainer-tmux-panes.log";
      };
    };
  };
}
