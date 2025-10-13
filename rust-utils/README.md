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

### Automatic build on updates
All utilities are automatically built when running:
- `wlr-check-update` (via `hooks/post-upgrade`)
- `wlr-build-rust-utils` (directly)

Binaries are installed to `~/.local/bin/`.

### Manual build
Each utility can be built independently:
```bash
cargo install --path rust-utils/niri-spacer --root ~/.local
```

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
