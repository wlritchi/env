{ config, pkgs, lib, ... }:

{
  systemd.user = {
    # Rclone copy service + timer
    services.rclone-copy = {
      Unit = { Description = "Run rclone copy on configured directories"; };
      Service = {
        Type = "oneshot";
        ExecStart = pkgs.writeShellScript "rclone-copy-runner" ''
          rclone_list="${config.xdg.configHome}/rclone-copy.list"
          if [ -f "$rclone_list" ]; then
            ecode=0
            shuf "$rclone_list" | while read line; do
              ${pkgs.rclone}/bin/rclone --bwlimit 750k copy "$line"
              ecode=$?
              [ $ecode -ne 0 ] && break
            done
            exit $ecode
          fi
        '';
      };
    };

    timers.rclone-copy = {
      Unit = {
        Description = "Run rclone-copy on boot and roughly every hour";
      };
      Timer = {
        OnBootSec = "5m";
        OnUnitActiveSec = "1h";
        RandomizedDelaySec = "5m";
      };
      Install = { WantedBy = [ "timers.target" ]; };
    };

    # Git sync service + timer
    # Note: Requires git-sync script to be available
    services.git-sync = {
      Unit = { Description = "Run git-sync on configured repos"; };
      Service = {
        Type = "oneshot";
        ExecStart = pkgs.writeShellScript "git-sync-runner" ''
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
        '';
      };
    };

    timers.git-sync = {
      Unit = {
        Description =
          "Run git-sync on configured repos on boot and roughly every hour";
      };
      Timer = {
        OnBootSec = "5m";
        OnUnitActiveSec = "1h";
        RandomizedDelaySec = "5m";
      };
      Install = { WantedBy = [ "timers.target" ]; };
    };

    # Tmux/Niri workspace tracker service + timer
    services.wlr-tmux-niri-tracker = {
      Unit = {
        Description = "Track tmux sessions to niri workspaces";
        Documentation =
          "file://${config.home.homeDirectory}/.wlrenv/TMUX_NIRI_WORKSPACE_TRACKING.md";
        ConditionEnvironment = "NIRI_SOCKET";
      };
      Service = {
        Type = "oneshot";
        ExecStart = "${pkgs.wlrenv}/bin/wlr-niri-track-terminals";
      };
    };

    timers.wlr-tmux-niri-tracker = {
      Unit = {
        Description =
          "Periodically track tmux sessions to niri workspaces every 30 seconds";
        Documentation =
          "file://${config.home.homeDirectory}/.wlrenv/TMUX_NIRI_WORKSPACE_TRACKING.md";
      };
      Timer = {
        OnBootSec = "30s";
        OnUnitActiveSec = "30s";
        Unit = "wlr-tmux-niri-tracker.service";
      };
      Install = { WantedBy = [ "timers.target" ]; };
    };

    # Tmux pane backup service + timer
    services.tmux-pane-backup = {
      Unit = { Description = "Back up tmux-resurrect pane contents"; };
      Service = {
        Type = "oneshot";
        ExecStart =
          "${config.home.homeDirectory}/.wlrenv/bin/tmux/wlr-tmux-pane-backup";
      };
    };

    timers.tmux-pane-backup = {
      Unit = {
        Description = "Back up tmux-resurrect pane contents every 6 minutes";
      };
      Timer = {
        OnBootSec = "1min";
        OnUnitActiveSec = "6min";
        Unit = "tmux-pane-backup.service";
      };
      Install = { WantedBy = [ "timers.target" ]; };
    };
  };
}
