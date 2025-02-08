#!/bin/bash
set -euo pipefail

resurrect_dir="$HOME/.local/share/tmux/resurrect"
if ! [ -d "$resurrect_dir" ]; then
    resurrect_dir="$HOME/.tmux/resurrect"
fi

if ! [ -d "$resurrect_dir" ]; then
    echo "No tmux-resurrect directory, nothing to back up"
    exit
fi

timestamp="$(date +"%Y-%m-%dT%H%M%S")"

src_path="$resurrect_dir/pane_contents.tar.gz"
dst_path="$resurrect_dir/pane_contents.$timestamp.tar.gz"

if [ -f "$src_path" ]; then
    cp "$src_path" "$dst_path"
else
    echo "No pane contents at $src_path, nothing to back up"
fi
