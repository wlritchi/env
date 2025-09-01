#!/usr/bin/env bash
# Wrapper script for running cargo commands on all Rust projects in rust-utils/
set -euo pipefail

CARGO_CMD="${1:-}"
shift || true

if [ -z "$CARGO_CMD" ]; then
    echo "Usage: $0 <cargo-subcommand> [args...]" >&2
    exit 1
fi

# Find all Cargo.toml files in rust-utils subdirectories (excluding target dirs)
find rust-utils -name Cargo.toml -not -path '*/target/*' | while read -r cargo_toml; do
    project_dir=$(dirname "$cargo_toml")
    echo "Running cargo $CARGO_CMD in $project_dir" >&2
    (cd "$project_dir" && cargo "$CARGO_CMD" "$@")
done
