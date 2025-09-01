use std::process::Command;

/// Test CLI argument validation
#[test]
fn test_cli_help_flag() {
    let output = Command::new("cargo")
        .args(["run", "--", "--help"])
        .output()
        .expect("Failed to execute command");

    assert!(output.status.success());
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(stdout.contains("niri-spacer"));
    assert!(stdout.contains("utility"));
    assert!(stdout.contains("Usage:"));
}

#[test]
fn test_cli_version_flag() {
    let output = Command::new("cargo")
        .args(["run", "--", "--version"])
        .output()
        .expect("Failed to execute command");

    assert!(output.status.success());
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(stdout.contains("niri-spacer"));
    assert!(stdout.contains("0.1.0")); // Should match version in Cargo.toml
}

#[test]
fn test_invalid_window_count_cli() {
    // Test with zero windows - should fail on argument parsing
    let output = Command::new("cargo")
        .args(["run", "--", "0"])
        .env("NIRI_SOCKET", "/nonexistent/socket") // Prevent actual niri connection
        .output()
        .expect("Failed to execute command");

    assert!(!output.status.success());
    // CLI should fail, but we don't test exact error message format here
}

#[test]
fn test_invalid_high_window_count_cli() {
    // Test with too many windows - should fail
    let output = Command::new("cargo")
        .args(["run", "--", "100"])
        .env("NIRI_SOCKET", "/nonexistent/socket") // Prevent actual niri connection
        .output()
        .expect("Failed to execute command");

    assert!(!output.status.success());
    // CLI should fail, but we don't test exact error message format here
}

#[test]
fn test_valid_window_count_cli() {
    // Test with valid window count - should fail on socket connection, not validation
    let output = Command::new("cargo")
        .args(["run", "--", "5"])
        .env("NIRI_SOCKET", "/nonexistent/socket") // This will cause a socket error
        .output()
        .expect("Failed to execute command");

    assert!(!output.status.success());
    let stderr = String::from_utf8_lossy(&output.stderr);
    // Should not contain window count validation error
    assert!(!stderr.contains("Window count must be between"));
}

#[test]
fn test_stats_flag_cli() {
    // Test stats flag
    let output = Command::new("cargo")
        .args(["run", "--", "--stats"])
        .env("NIRI_SOCKET", "/nonexistent/socket") // This will cause a socket error
        .output()
        .expect("Failed to execute command");

    assert!(!output.status.success());
    let stderr = String::from_utf8_lossy(&output.stderr);
    // Should fail on socket connection, not argument parsing
    assert!(!stderr.contains("invalid"));
}

#[test]
fn test_boundary_window_counts_cli() {
    // Test boundary values for window count validation

    // Test below minimum (should fail)
    let output = Command::new("cargo")
        .args(["run", "--", "0"])
        .env("NIRI_SOCKET", "/nonexistent/socket")
        .output()
        .expect("Failed to execute command");

    assert!(!output.status.success());

    // Test above maximum (should fail)
    let output = Command::new("cargo")
        .args(["run", "--", "999"])
        .env("NIRI_SOCKET", "/nonexistent/socket")
        .output()
        .expect("Failed to execute command");

    assert!(!output.status.success());
}

/// Test basic CLI behavior consistency
#[test]
fn test_cli_basic_functionality() {
    // Test that CLI handles argument parsing consistently

    // Invalid argument should fail
    let output = Command::new("cargo")
        .args(["run", "--", "invalid"])
        .env("NIRI_SOCKET", "/nonexistent/socket")
        .output()
        .expect("Failed to execute command");

    assert!(!output.status.success());

    // Valid number should proceed to socket connection (and fail there)
    let output = Command::new("cargo")
        .args(["run", "--", "5"])
        .env("NIRI_SOCKET", "/nonexistent/socket")
        .output()
        .expect("Failed to execute command");

    assert!(!output.status.success());
}

/// Test CLI flags work as expected
#[test]
fn test_cli_flags() {
    // Test that various flags are recognized (they'll fail on socket connection)
    let flags = ["--stats", "--info"];

    for flag in &flags {
        let output = Command::new("cargo")
            .args(["run", "--", flag])
            .env("NIRI_SOCKET", "/nonexistent/socket")
            .output()
            .expect("Failed to execute command");

        // Should fail on socket connection, not flag parsing
        assert!(!output.status.success());
    }
}

/// Test that running without arguments works (uses defaults)
#[test]
fn test_default_behavior_cli() {
    // Running without count should use default and proceed to socket connection
    let output = Command::new("cargo")
        .args(["run"])
        .env("NIRI_SOCKET", "/nonexistent/socket")
        .output()
        .expect("Failed to execute command");

    // Should fail on socket connection, not on argument validation
    assert!(!output.status.success());
}
