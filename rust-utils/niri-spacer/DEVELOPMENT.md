# Development Guide

This document provides instructions for setting up the development environment and running quality checks for the niri-spacer project.

## Prerequisites

- **Rust** (MSRV 1.70 or later)
- **uv** for Python tool management (for pre-commit hooks)
- **git** for version control

## Development Setup

### 1. Install Required Tools

```bash
# Install quality assurance tools
cargo install cargo-audit cargo-deny cargo-llvm-cov

# Install pre-commit using uv
uv tool install pre-commit
```

### 2. Set Up Pre-commit Hooks

```bash
# Install pre-commit hooks
uv tool run pre-commit install

# Test hooks on all files (optional)
uv tool run pre-commit run --all-files
```

## Quality Checks

The project uses multiple quality assurance tools to ensure code quality, security, and consistency.

### Code Formatting

```bash
# Check formatting
cargo fmt --check

# Auto-format code
cargo fmt
```

### Linting

```bash
# Run clippy with all warnings as errors
cargo clippy --all-targets --all-features -- -D warnings

# Run clippy with automatic fixes (when possible)
cargo clippy --all-targets --all-features --fix
```

### Testing

```bash
# Run all tests
cargo test --all-features

# Run only unit tests
cargo test --lib

# Run only integration tests
cargo test --test integration_tests
cargo test --test property_tests
cargo test --test cli_tests

# Run with verbose output
cargo test --all-features -- --nocapture
```

### Security Audits

```bash
# Check for known security vulnerabilities
cargo audit

# Check licenses and banned dependencies
cargo deny check
```

### Code Coverage

```bash
# Generate coverage report (LCOV format)
cargo llvm-cov --all-features --lcov --output-path lcov.info

# Generate coverage report (HTML format)
cargo llvm-cov --all-features --html

# Open HTML coverage report
open target/llvm-cov/html/index.html  # macOS
xdg-open target/llvm-cov/html/index.html  # Linux
```

### Building

```bash
# Build in debug mode
cargo build

# Build in release mode (optimized)
cargo build --release

# Build documentation
cargo doc --no-deps --all-features

# Build and open documentation
cargo doc --no-deps --all-features --open
```

### Benchmarks

```bash
# Run performance benchmarks
cargo bench

# Run specific benchmark
cargo bench window_validation
```

## Full Quality Pipeline

To run all quality checks locally (what CI runs):

```bash
#!/bin/bash
set -e

echo "🔍 Running code formatting check..."
cargo fmt --check

echo "🔍 Running clippy..."
cargo clippy --all-targets --all-features -- -D warnings

echo "🔍 Running tests..."
cargo test --all-features

echo "🔍 Building in release mode..."
cargo build --release

echo "🔍 Running security audit..."
cargo audit

echo "🔍 Checking licenses and dependencies..."
cargo deny check

echo "🔍 Generating code coverage..."
cargo llvm-cov --all-features --lcov --output-path lcov.info

echo "✅ All quality checks passed!"
```

Save this as `scripts/quality-check.sh` and run:

```bash
chmod +x scripts/quality-check.sh
./scripts/quality-check.sh
```

## Pre-commit Hooks

The project uses pre-commit hooks to automatically run quality checks before commits. The hooks include:

- **Rust formatting** (`cargo fmt`)
- **Rust linting** (`cargo clippy`)
- **Test execution** (`cargo test`)
- **Security audit** (`cargo audit`) - skipped in CI
- **License/dependency check** (`cargo deny`) - skipped in CI
- **Build verification** (`cargo build`)
- **General file checks** (trailing whitespace, EOF, YAML/TOML validation)

### Skipping Hooks (Emergency)

If you need to bypass pre-commit hooks in an emergency:

```bash
git commit --no-verify -m "Emergency commit message"
```

**Note**: This should be rare and followed by fixing any quality issues.

## Continuous Integration

The project uses GitHub Actions for CI with multiple jobs:

- **Test Matrix**: Tests on stable, beta, and MSRV Rust versions
- **Security Audit**: Runs `cargo audit` and `cargo deny check`  
- **Code Coverage**: Generates coverage reports and uploads to Codecov
- **Cross-platform Build**: Tests builds for x86_64 and aarch64 Linux
- **Documentation**: Builds and checks documentation

### CI Configuration

The CI configuration is in `.github/workflows/ci.yml`. Key features:

- Caching for faster builds
- Matrix testing across Rust versions
- Comprehensive quality checks
- Coverage reporting
- Cross-compilation testing

## Development Workflow

### 1. Before Starting Work

```bash
# Update dependencies
cargo update

# Ensure everything works
cargo test --all-features
```

### 2. During Development

```bash
# Run tests frequently
cargo test

# Check formatting as you go
cargo fmt

# Run clippy for early issue detection
cargo clippy
```

### 3. Before Committing

Pre-commit hooks will run automatically, but you can run them manually:

```bash
# Test your changes
cargo test --all-features

# Ensure code is formatted
cargo fmt

# Run full quality pipeline
uv tool run pre-commit run --all-files
```

### 4. Before Pushing

```bash
# Final quality check
cargo test --all-features
cargo clippy --all-targets --all-features -- -D warnings
cargo build --release

# Ensure commit messages are clear
git log --oneline -n 5
```

## Project Structure

```
niri-spacer/
├── src/                    # Source code
│   ├── lib.rs             # Library root and public API
│   ├── main.rs            # CLI application entry point
│   ├── error.rs           # Error handling types
│   ├── niri.rs            # niri IPC communication
│   ├── session.rs         # Session detection and validation
│   ├── window.rs          # Window management
│   └── workspace.rs       # Workspace operations
├── tests/                 # Integration tests
│   ├── integration_tests.rs  # Main integration tests
│   ├── property_tests.rs     # Property-based tests
│   └── cli_tests.rs          # CLI-specific tests
├── benches/               # Performance benchmarks
│   └── window_creation.rs
├── .github/workflows/     # CI configuration
│   └── ci.yml
└── config files...        # Various tool configurations
```

## Configuration Files

- **`.pre-commit-config.yaml`**: Pre-commit hook configuration
- **`clippy.toml`**: Clippy linting rules
- **`rustfmt.toml`**: Code formatting preferences  
- **`deny.toml`**: License and dependency policies
- **`.yamllint.yml`**: YAML linting configuration
- **`.gitignore`**: Git ignore patterns

## Troubleshooting

### Pre-commit Issues

```bash
# Update pre-commit hooks
uv tool run pre-commit autoupdate

# Clear pre-commit cache
uv tool run pre-commit clean

# Reinstall hooks
uv tool run pre-commit uninstall
uv tool run pre-commit install
```

### Coverage Issues

```bash
# Clean coverage data
cargo llvm-cov clean

# Install required components
rustup component add llvm-tools-preview
```

### Dependency Issues

```bash
# Update to latest compatible versions
cargo update

# Check for outdated dependencies
cargo tree --outdated
```

## Best Practices

### Code Quality

1. **Write tests first** when adding new features
2. **Run tests locally** before pushing changes
3. **Keep functions small** and focused on single responsibilities
4. **Document public APIs** with comprehensive examples
5. **Handle errors explicitly** rather than using `unwrap()` or `expect()`

### Performance

1. **Profile before optimizing** using `cargo bench`
2. **Prefer iterator chains** over manual loops
3. **Use appropriate data structures** for the use case
4. **Minimize allocations** in hot paths

### Security

1. **Validate all inputs** at API boundaries
2. **Use type system** to enforce invariants
3. **Regular security audits** with `cargo audit`
4. **Review dependency licenses** with `cargo deny`

### Documentation

1. **Keep README up-to-date** with current functionality
2. **Document complex algorithms** and design decisions
3. **Provide usage examples** for public functions
4. **Maintain this development guide** as processes evolve

## Getting Help

- **Issues**: Report bugs and feature requests on GitHub Issues
- **Discussions**: Ask questions in GitHub Discussions
- **Documentation**: Check `cargo doc --open` for API documentation
- **Tests**: Look at test files for usage examples