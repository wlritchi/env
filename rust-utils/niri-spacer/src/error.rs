use std::io;
use std::process::ExitStatus;

/// Custom error types for niri-spacer operations
#[derive(thiserror::Error, Debug)]
pub enum NiriSpacerError {
    #[error("Not running in a niri session")]
    NotNiriSession,

    #[error("NIRI_SOCKET environment variable not set")]
    NoSocketPath,

    #[error("Invalid socket path: {0}")]
    InvalidSocketPath(String),

    #[error("Failed to connect to niri socket: {0}")]
    SocketConnection(#[from] io::Error),

    #[error("JSON serialization error: {0}")]
    JsonSerialization(#[from] serde_json::Error),

    #[error("IPC communication error: {0}")]
    IpcError(String),

    #[error("Window count must be between 1 and 50, got {0}")]
    InvalidWindowCount(u32),

    #[error("Failed to spawn process: {0}")]
    ProcessSpawn(io::Error),

    #[error("Process failed with exit code: {0:?}")]
    ProcessFailed(ExitStatus),

    #[error("Timeout waiting for operation to complete")]
    OperationTimeout,

    #[error("niri returned error: {0}")]
    NiriError(String),

    #[error("Unexpected response format from niri")]
    UnexpectedResponse,

    #[error("Workspace not found: {0}")]
    WorkspaceNotFound(u64),

    #[error("Window not found: {0}")]
    WindowNotFound(u64),

    #[error("Failed to resize window: {0}")]
    WindowResize(String),

    #[error("Failed to move window: {0}")]
    WindowMove(String),

    #[error("Failed to focus window: {0}")]
    WindowFocus(String),
}

pub type Result<T> = std::result::Result<T, NiriSpacerError>;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_error_display_messages() {
        let errors = vec![
            (
                NiriSpacerError::NotNiriSession,
                "Not running in a niri session",
            ),
            (
                NiriSpacerError::NoSocketPath,
                "NIRI_SOCKET environment variable not set",
            ),
            (
                NiriSpacerError::InvalidSocketPath("test".to_string()),
                "Invalid socket path: test",
            ),
            (
                NiriSpacerError::IpcError("test".to_string()),
                "IPC communication error: test",
            ),
            (
                NiriSpacerError::InvalidWindowCount(100),
                "Window count must be between 1 and 50, got 100",
            ),
            (
                NiriSpacerError::OperationTimeout,
                "Timeout waiting for operation to complete",
            ),
            (
                NiriSpacerError::NiriError("test".to_string()),
                "niri returned error: test",
            ),
            (
                NiriSpacerError::UnexpectedResponse,
                "Unexpected response format from niri",
            ),
            (
                NiriSpacerError::WorkspaceNotFound(5),
                "Workspace not found: 5",
            ),
            (
                NiriSpacerError::WindowNotFound(123),
                "Window not found: 123",
            ),
        ];

        for (error, expected_msg) in errors {
            assert_eq!(error.to_string(), expected_msg);
        }
    }

    #[test]
    fn test_error_from_io_error() {
        let io_err =
            std::io::Error::new(std::io::ErrorKind::ConnectionRefused, "Connection refused");
        let spacer_err: NiriSpacerError = io_err.into();

        match spacer_err {
            NiriSpacerError::SocketConnection(_) => {},
            _ => panic!("Expected SocketConnection error"),
        }
    }

    #[test]
    fn test_error_from_json_error() {
        let json_err = serde_json::from_str::<serde_json::Value>("invalid json").unwrap_err();
        let spacer_err: NiriSpacerError = json_err.into();

        match spacer_err {
            NiriSpacerError::JsonSerialization(_) => {},
            _ => panic!("Expected JsonSerialization error"),
        }
    }

    #[test]
    #[allow(clippy::unnecessary_literal_unwrap)]
    fn test_result_type_alias() {
        let success: Result<i32> = Ok(42);
        assert_eq!(success.expect("Should be Ok"), 42);

        let failure: Result<i32> = Err(NiriSpacerError::NotNiriSession);
        assert!(failure.is_err());
    }

    #[test]
    fn test_error_debug_format() {
        let error = NiriSpacerError::InvalidWindowCount(25);
        let debug_str = format!("{:?}", error);
        assert!(debug_str.contains("InvalidWindowCount"));
        assert!(debug_str.contains("25"));
    }
}
