# Rust Utilities

This directory contains standalone Rust utilities that are part of the wlrenv toolkit.

## Structure

Each utility is a standalone Rust crate with its own `Cargo.toml` and `Cargo.lock`:

```
rust-utils/
├── niri-spacer/
│   ├── Cargo.toml
│   ├── Cargo.lock
│   └── src/
└── another-tool/
    ├── Cargo.toml
    ├── Cargo.lock
    └── src/
```

## Building

### Deployment (Nix)
All utilities are built and installed via Nix derivations defined in `machines/pkgs/`:
- Automatically built when running `wlr-check-update` (via `hooks/post-upgrade` → `wlr-nix-rebuild`)
- Binaries installed to `~/.nix-profile/bin/` (takes precedence in PATH)
- System dependencies (Wayland libraries, etc.) are automatically provided

### Development
For local development and testing:
```bash
cd rust-utils/niri-spacer
cargo build
cargo test
cargo run -- --help
```

Standard Rust tooling works as expected. Changes won't be deployed until you run `wlr-nix-rebuild`.

### Manual installation (without Nix)
Each utility can be built independently with cargo:
```bash
cargo install --path rust-utils/niri-spacer --root ~/.local
```
Note: System dependencies must be manually installed (see each utility's README).

### Updating dependencies
Run `wlr-update-locks` to update all `Cargo.lock` files.

## Design principles

1. **Independence**: Each utility is a standalone crate that can be moved to its own repository
2. **No workspace**: Utilities are NOT part of a Cargo workspace to maintain independence
3. **Portable**: Each utility can be built with standard `cargo install` without requiring Nix
4. **Self-contained**: All dependencies should be specified in the utility's own `Cargo.toml`

## Adding a new utility

1. Create a new directory: `mkdir rust-utils/my-tool`
2. Initialize with cargo: `cd rust-utils/my-tool && cargo init`
3. Add your code and dependencies
4. Test with: `cargo install --path . --root ~/.local`
5. Run `wlr-build-rust-utils` to verify it builds correctly
