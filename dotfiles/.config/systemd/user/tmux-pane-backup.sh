#!/bin/bash

timestamp=$(date +"%Y-%m-%dT%H%M%S")

src_path="${HOME}/.local/share/tmux/resurrect/pane_contents.tar.gz"
dst_path="${HOME}/.local/share/tmux/resurrect/pane_contents.${timestamp}.tar.gz"

if [ -f "$src_path" ]; then
    cp "$src_path" "$dst_path"
fi
