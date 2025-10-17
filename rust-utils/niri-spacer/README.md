# niri-spacer

A persistent utility to spawn and manage placeholder windows in niri workspaces for improved tiling behavior.

## System Dependencies

This tool requires Wayland development libraries to build:

### Arch Linux / Manjaro
```bash
sudo pacman -S libxkbcommon wayland wayland-protocols
```

### Ubuntu / Debian
```bash
sudo apt install libxkbcommon-dev libwayland-dev wayland-protocols
```

### Fedora / RHEL
```bash
sudo dnf install libxkbcommon-devel wayland-devel wayland-protocols-devel
```

### NixOS / Nix
When using this repository's flake, dependencies are automatically provided by the Nix derivation.

## Building

### With wlrenv (recommended)
If using this repository, niri-spacer is built automatically:
```bash
wlr-nix-rebuild
```
Binary installed to `~/.nix-profile/bin/niri-spacer`.

### Manual build (development)
```bash
cargo build
cargo run -- --help
```

### Standalone installation
```bash
cargo install --path . --root ~/.local
```
Requires system dependencies listed above.

## Usage

See `niri-spacer --help` for usage information.
