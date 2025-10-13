# .wlrenv Guidelines

## Repository Overview
This repository contains dotfiles, shell scripts, and utility functions for Linux/Unix environments. It provides a consistent shell experience across multiple machines with smart handling of platform differences.

## Core Commands
- Sync dotfiles: `wlr-sync-dotfiles`
- Check for updates: `wlr-check-update`
- Message formatting: `wlr-err`, `wlr-warn`, `wlr-ask`, `wlr-good`
- tmux helpers: `wlr-ensure-tmux-running`, `wlr-fix-tmux-resurrect`, `wlr-open-tmux-sessions`

## Key Environment Scripts
- `bootstrap.sh`: Initial setup script
- `env.bash`: Main environment setup script
- `env`: Environment variables file
- `xonshrc.py`: Configuration for xonsh shell

## Module Organization
- Git utilities: `bin/git/` and aliases in `bin/aliases/`
- Cryptography: `bin/crypto/` (JWT tools)
- SSH/Mosh: `bin/ssh/`
- Kubernetes: `bin/k8s/`
- Display servers: `bin/wayland/`, `bin/xorg/`
- Command wrappers: `bin/shims/`
- Web services: `bin/webservice/`
- Third-party tools: `bin/vendor/`

## Coding Style
- Shell scripts should use `#!/usr/bin/env bash`
- Always include `set -euo pipefail` in bash scripts
- Use 4-space indentation
- Descriptive function and variable names
- Functions should be lowercase with underscores
- Error handling with informative messages (see `wlr-err`, `wlr-warn`, `wlr-ask`)
- Platform compatibility: Support GNU/Linux and BSD/macOS (see stat command usage)
- Document dependencies at the top of scripts
- Use relative paths from $WLR_ENV_PATH when possible
- Feature detection with fallbacks for cross-platform compatibility

## Repository Structure
- `bin/`: Utility scripts and commands, organized by functionality
- `assets/`: Media files (sounds, images)
- `dotfiles/`: Configuration files to be symlinked to home directory
- `patches/`: Host and OS-specific patches for dotfiles
- `rust-utils/`: Standalone Rust utilities (see rust-utils/README.md for details)
  - Built with `wlr-build-rust-utils` or `cargo install --path rust-utils/<tool> --root ~/.local`
  - Binaries are installed to `~/.local/bin/` (e.g., `niri-spacer` → `~/.local/bin/niri-spacer`)
  - Automatically built on `wlr-check-update` via `hooks/post-upgrade`

## Nix Integration
This repository uses Nix Flakes with home-manager for declarative package and environment management:
- **Flake configuration**: `flake.nix` defines home-manager configurations for different hosts
- **Package declarations**: `machines/common.nix` declares common packages across all systems
- **Platform-specific configs**: `machines/linux.nix` and `machines/darwin.nix` for OS-specific settings
- **Host-specific overrides**: `machines/hosts/{hostname}.nix` for machine-specific customizations
- **Apply changes**: `wlr-nix-rebuild` to rebuild home-manager environment
- **Update dependencies**: `wlr-update-locks` updates flake.lock (and other lockfiles)
- **Nix profile**: `~/.nix-profile/bin` is automatically added to PATH in env.bash
- **Special integrations**: Uses krew2nix for declarative kubectl plugin management

## Environment Integration
- Integration with development environment tools (nodenv, pyenv, etc.)
- Terminal multiplexer support (tmux, zellij)
- Enhanced shell experience (xonsh)
- Command wrapper pattern for enhanced functionality

## Claude Notes
After each session, Claude should update this file with any important learnings about working with this repository.
