#!/usr/bin/env bash
# Wrapper script for running cargo commands on all Rust projects in rust-utils/
set -euo pipefail

CARGO_CMD="${1:-}"
shift || true

if [ -z "$CARGO_CMD" ]; then
    echo "Usage: $0 <cargo-subcommand> [args...]" >&2
    exit 1
fi

# Run cargo in the rust devShell of flake.nix, so that the hooks use the
# rustc that machines/pkgs builds the utilities with, not a rustup toolchain.
# The shell is kept as a profile, which is also a GC root. It is built again
# when flake.nix or flake.lock changes.
run=()
if command -v nix >/dev/null 2>&1; then
    root="$(git rev-parse --show-toplevel)"
    profile="${XDG_STATE_HOME:-$HOME/.local/state}/wlrenv/rust-dev-shell"
    key="$(git hash-object "$root/flake.nix" "$root/flake.lock")"
    if ! [ -e "$profile" ] || [ "$(cat "$profile.key" 2>/dev/null)" != "$key" ]; then
        mkdir -p "${profile%/*}"
        echo "Building the rust dev shell" >&2
        nix develop --impure --profile "$profile" "$root#rust" --command true
        printf '%s\n' "$key" >"$profile.key"
    fi
    run=(nix develop "$profile" --command)
fi

# Find all Cargo.toml files in rust-utils subdirectories (excluding target dirs)
find rust-utils -name Cargo.toml -not -path '*/target/*' | while read -r cargo_toml; do
    project_dir=$(dirname "$cargo_toml")
    echo "Running cargo $CARGO_CMD in $project_dir" >&2
    (cd "$project_dir" && ${run[@]+"${run[@]}"} cargo "$CARGO_CMD" "$@")
done
