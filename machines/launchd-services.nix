{ config, pkgs, lib, ... }:

{
  launchd.agents = {
    # Rclone copy - runs every hour
    rclone-copy = {
      enable = true;
      config = {
        ProgramArguments = [
          "${pkgs.bash}/bin/bash"
          "-c"
          ''
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
          ''
        ];
        RunAtLoad = true;
        StartInterval = 3600; # Run every hour
        StandardOutPath =
          "${config.home.homeDirectory}/Library/Logs/rclone-copy.log";
        StandardErrorPath =
          "${config.home.homeDirectory}/Library/Logs/rclone-copy.log";
      };
    };

    # Git sync - runs every hour
    # Note: Requires git-sync script to be available
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
        StandardOutPath =
          "${config.home.homeDirectory}/Library/Logs/git-sync.log";
        StandardErrorPath =
          "${config.home.homeDirectory}/Library/Logs/git-sync.log";
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
        StandardOutPath =
          "${config.home.homeDirectory}/Library/Logs/tmux-pane-backup.log";
        StandardErrorPath =
          "${config.home.homeDirectory}/Library/Logs/tmux-pane-backup.log";
      };
    };

    # Aerospace ghost window cleanup - runs every 30 seconds
    aerospace-ghost-cleanup = {
      enable = true;
      config = {
        ProgramArguments = [
          "${pkgs.bash}/bin/bash"
          "-c"
          ''
            # Get all windows
            output=$(${pkgs.aerospace}/bin/aerospace list-windows --all)

            # If no output, exit
            [ -z "$output" ] && exit 0

            # Arrays to track windows
            ghost_windows=()
            has_non_ghost=false

            # Parse each line (format: window_id | app_name | title)
            while IFS='|' read -r window_id app_name title; do
              # Trim whitespace
              window_id=$(echo "$window_id" | xargs)
              title=$(echo "$title" | xargs)

              if [ -z "$title" ]; then
                # This is a ghost window
                ghost_windows+=("$window_id")
              else
                # This is a non-ghost window
                has_non_ghost=true
              fi
            done <<< "$output"

            # If only ghost windows exist (lock screen is up), exit
            if [ "$has_non_ghost" = false ]; then
              exit 0
            fi

            # Close each ghost window
            for id in "''${ghost_windows[@]}"; do
              ${pkgs.aerospace}/bin/aerospace close --window-id "$id"
            done
          ''
        ];
        RunAtLoad = true;
        StartInterval = 30; # Run every 30 seconds
        StandardOutPath =
          "${config.home.homeDirectory}/Library/Logs/aerospace-ghost-cleanup.log";
        StandardErrorPath =
          "${config.home.homeDirectory}/Library/Logs/aerospace-ghost-cleanup.log";
      };
    };
  };
}
