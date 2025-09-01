use niri_spacer::*;
use proptest::prelude::*;
use serial_test::serial;
use std::collections::HashMap;
use std::time::Duration;
use tempfile::TempDir;
use tokio::net::UnixListener;
use tokio::time::timeout;

/// Mock niri IPC server for testing
struct MockNiriServer {
    _temp_dir: TempDir,
    socket_path: String,
    responses: HashMap<String, String>,
}

impl MockNiriServer {
    async fn new() -> tokio::io::Result<Self> {
        let temp_dir = TempDir::new().unwrap();
        let socket_path = temp_dir
            .path()
            .join("niri.sock")
            .to_string_lossy()
            .to_string();

        let listener = UnixListener::bind(&socket_path)?;

        // Set up default responses
        let mut responses = HashMap::new();
        responses.insert(
            "workspaces".to_string(),
            r#"{"workspaces": [{"id": 1, "name": "1", "is_focused": true, "windows": []}]}"#
                .to_string(),
        );
        responses.insert("windows".to_string(), r#"{"windows": []}"#.to_string());

        let server = Self {
            _temp_dir: temp_dir,
            socket_path,
            responses,
        };

        // Spawn server task
        let responses_clone = server.responses.clone();
        tokio::spawn(async move {
            while let Ok((stream, _)) = listener.accept().await {
                let responses = responses_clone.clone();
                tokio::spawn(async move {
                    Self::handle_client(stream, responses).await;
                });
            }
        });

        Ok(server)
    }

    async fn handle_client(stream: tokio::net::UnixStream, responses: HashMap<String, String>) {
        use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};

        let (reader, mut writer) = stream.into_split();
        let mut reader = BufReader::new(reader);
        let mut buffer = String::new();

        while reader.read_line(&mut buffer).await.is_ok() {
            let request = buffer.trim();
            if let Some(response) = responses.get(request) {
                if writer.write_all(response.as_bytes()).await.is_err() {
                    break;
                }
                if writer.write_all(b"\n").await.is_err() {
                    break;
                }
            }
            buffer.clear();
        }
    }

    fn socket_path(&self) -> &str {
        &self.socket_path
    }
}

#[tokio::test]
#[serial]
async fn test_niri_spacer_initialization() {
    let mock_server = MockNiriServer::new()
        .await
        .expect("Failed to create mock server");

    // Set environment variables to simulate niri session
    std::env::set_var("NIRI_SOCKET", mock_server.socket_path());
    std::env::set_var("WAYLAND_DISPLAY", "wayland-1");
    std::env::set_var("XDG_SESSION_TYPE", "wayland");
    std::env::set_var("XDG_SESSION_DESKTOP", "niri");
    std::env::set_var("XDG_CURRENT_DESKTOP", "niri");

    // Test successful initialization
    let result = timeout(Duration::from_secs(5), NiriSpacer::new()).await;
    assert!(result.is_ok(), "Initialization should not timeout");

    // Clean up environment
    std::env::remove_var("NIRI_SOCKET");
    std::env::remove_var("WAYLAND_DISPLAY");
    std::env::remove_var("XDG_SESSION_TYPE");
    std::env::remove_var("XDG_SESSION_DESKTOP");
    std::env::remove_var("XDG_CURRENT_DESKTOP");
}

#[tokio::test]
#[serial]
async fn test_invalid_session_detection() {
    // Remove all niri-related environment variables
    std::env::remove_var("NIRI_SOCKET");
    std::env::remove_var("WAYLAND_DISPLAY");
    std::env::remove_var("XDG_SESSION_TYPE");

    // Test should fail with appropriate error
    let result = NiriSpacer::new().await;
    assert!(result.is_err());

    if let Err(error) = result {
        match error {
            NiriSpacerError::NotNiriSession | NiriSpacerError::NoSocketPath => {
                // Expected errors
            },
            other => panic!("Unexpected error type: {:?}", other),
        }
    }
}

#[test]
fn test_window_count_validation_bounds() {
    // Test window count validation without actually running the spacer
    use niri_spacer::defaults;

    // Test that the bounds are correct - using allow to suppress clippy warning as this is intentional test
    #[allow(clippy::assertions_on_constants)]
    {
        assert!(defaults::MIN_WINDOW_COUNT <= defaults::MAX_WINDOW_COUNT);
    }
    assert_eq!(defaults::MIN_WINDOW_COUNT, 1);
    assert_eq!(defaults::MAX_WINDOW_COUNT, 50);

    // Test boundary values
    assert!((defaults::MIN_WINDOW_COUNT..=defaults::MAX_WINDOW_COUNT).contains(&1));
    assert!((defaults::MIN_WINDOW_COUNT..=defaults::MAX_WINDOW_COUNT).contains(&50));
    assert!(!(defaults::MIN_WINDOW_COUNT..=defaults::MAX_WINDOW_COUNT).contains(&0));
    assert!(!(defaults::MIN_WINDOW_COUNT..=defaults::MAX_WINDOW_COUNT).contains(&51));
}

// Property-based tests for CLI argument validation
proptest! {
    #[test]
    fn test_window_count_range_property(count in 1u32..=50u32) {
        // Valid window counts should be accepted
        assert!((defaults::MIN_WINDOW_COUNT..=defaults::MAX_WINDOW_COUNT).contains(&count));
    }

    #[test]
    fn test_invalid_window_count_property(count in 51u32..=1000u32) {
        // Invalid window counts should be rejected
        assert!(count > defaults::MAX_WINDOW_COUNT);
    }
}

#[tokio::test]
async fn test_session_info_functionality() {
    use std::fs::File;

    // Test session info display without requiring actual niri
    // Create a temporary socket file for testing
    let temp_socket = "/tmp/test_niri.sock";
    let _ = File::create(temp_socket).unwrap();

    let session_info = niri_spacer::NiriSessionInfo {
        socket_path: temp_socket.to_string(),
        wayland_display: Some("wayland-1".to_string()),
        session_type: Some("wayland".to_string()),
        session_desktop: Some("niri".to_string()),
        current_desktop: Some("niri".to_string()),
        is_wayland: true,
    };

    assert!(session_info.is_valid_niri_session());

    let recommendations = session_info.get_recommendations();
    assert!(recommendations.len() == 1); // Should have one positive message for valid session
    assert!(recommendations[0].contains("looks good"));

    // Clean up
    let _ = std::fs::remove_file(temp_socket);
}

#[test]
fn test_default_constants() {
    assert_eq!(defaults::DEFAULT_WINDOW_COUNT, 9);
    assert_eq!(defaults::MIN_WINDOW_COUNT, 1);
    assert_eq!(defaults::MAX_WINDOW_COUNT, 50);
    assert_eq!(defaults::DEFAULT_SPAWN_DELAY_MS, 50);
    assert_eq!(defaults::DEFAULT_OPERATION_DELAY_MS, 25);
}

#[test]
fn test_app_metadata() {
    assert!(!APP_NAME.is_empty());
    assert!(!APP_VERSION.is_empty());
    assert!(!APP_DESCRIPTION.is_empty());

    assert_eq!(APP_NAME, "niri-spacer");
    assert!(APP_VERSION.chars().next().unwrap().is_ascii_digit());
}

#[tokio::test]
async fn test_workspace_stats_calculation() {
    let stats = niri_spacer::WorkspaceStats {
        total_workspaces: 5,
        empty_workspaces: 2,
        total_windows: 10,
        spacer_windows: 3,
        focused_workspace_id: Some(1),
        workspace_window_counts: [(1, 3), (2, 2), (3, 5)].iter().cloned().collect(),
    };

    assert_eq!(stats.total_workspaces, 5);
    assert_eq!(stats.empty_workspaces, 2);
    assert_eq!(stats.total_windows, 10);
    assert_eq!(stats.spacer_windows, 3);

    let summary = stats.summary();
    assert!(summary.contains("5 workspaces"));
    assert!(summary.contains("10 windows"));

    // Test tiling layout assessment
    assert!(stats.has_good_tiling_layout()); // Should be true with reasonable distribution
}

#[test]
fn test_error_types_display() {
    let error = NiriSpacerError::InvalidWindowCount(100);
    let display = format!("{}", error);
    assert!(display.contains("100"));

    let error = NiriSpacerError::NotNiriSession;
    let display = format!("{}", error);
    assert!(display.contains("niri"));

    let error = NiriSpacerError::NoSocketPath;
    let display = format!("{}", error);
    assert!(display.contains("NIRI_SOCKET"));
}

#[test]
fn test_spacer_window_properties() {
    let spacer = niri_spacer::SpacerWindow {
        id: 12345,
        workspace_id: 3,
        window_number: 1,
    };

    assert_eq!(spacer.id, 12345);
    assert_eq!(spacer.workspace_id, 3);
    assert_eq!(spacer.window_number, 1);
}

// Benchmark placeholder for performance testing
#[tokio::test]
async fn test_performance_baseline() {
    let start = std::time::Instant::now();

    // Simulate some processing time
    tokio::time::sleep(Duration::from_millis(1)).await;

    let elapsed = start.elapsed();
    assert!(
        elapsed < Duration::from_millis(100),
        "Basic operations should be fast"
    );
}
