use crate::error::{NiriSpacerError, Result};
use std::env;
use std::path::Path;
use tracing::{debug, info, warn};

/// Session detection and validation for niri compositor
pub struct SessionValidator;

impl SessionValidator {
    /// Detect if we're running in a niri session
    pub fn detect_niri_session() -> Result<NiriSessionInfo> {
        debug!("Detecting niri session environment");

        // Check for NIRI_SOCKET environment variable
        let socket_path = env::var("NIRI_SOCKET").map_err(|_| NiriSpacerError::NoSocketPath)?;

        debug!("Found NIRI_SOCKET: {}", socket_path);

        // Validate socket path exists
        if !Path::new(&socket_path).exists() {
            return Err(NiriSpacerError::InvalidSocketPath(socket_path));
        }

        // Check if socket is accessible
        if let Err(e) = std::fs::metadata(&socket_path) {
            warn!("Cannot access niri socket at {}: {}", socket_path, e);
            return Err(NiriSpacerError::SocketConnection(e));
        }

        // Check for Wayland session
        let wayland_display = env::var("WAYLAND_DISPLAY").ok();
        debug!("WAYLAND_DISPLAY: {:?}", wayland_display);

        // Check for XDG session info
        let session_type = env::var("XDG_SESSION_TYPE").ok();
        let session_desktop = env::var("XDG_SESSION_DESKTOP").ok();
        let current_desktop = env::var("XDG_CURRENT_DESKTOP").ok();

        debug!("Session type: {:?}", session_type);
        debug!("Session desktop: {:?}", session_desktop);
        debug!("Current desktop: {:?}", current_desktop);

        // Validate this looks like a niri session
        let is_wayland = session_type.as_deref() == Some("wayland") || wayland_display.is_some();
        if !is_wayland {
            warn!("Not running in a Wayland session");
        }

        let session_info = NiriSessionInfo {
            socket_path,
            wayland_display,
            session_type,
            session_desktop,
            current_desktop,
            is_wayland,
        };

        info!("Detected niri session: {}", session_info.summary());
        Ok(session_info)
    }

    /// Run comprehensive environment validation
    pub fn validate_environment() -> Result<NiriSessionInfo> {
        debug!("Running comprehensive environment validation");

        // Detect niri session
        let session_info = Self::detect_niri_session()?;

        // Additional environment checks
        Self::check_permissions(&session_info.socket_path)?;

        info!("Environment validation completed successfully");
        Ok(session_info)
    }

    /// Run environment validation for native-only mode
    pub fn validate_environment_native_only() -> Result<NiriSessionInfo> {
        debug!("Running native-only environment validation");

        // Detect niri session
        let session_info = Self::detect_niri_session()?;

        // Validate Wayland environment
        Self::validate_wayland_environment()?;

        // Additional environment checks
        Self::check_permissions(&session_info.socket_path)?;

        info!("Native-only environment validation completed successfully");
        Ok(session_info)
    }

    /// Validate Wayland environment for native windows
    fn validate_wayland_environment() -> Result<()> {
        debug!("Checking Wayland environment for native windows");

        // Check for Wayland display
        if std::env::var("WAYLAND_DISPLAY").is_err() {
            warn!("WAYLAND_DISPLAY not set - native windows may not work");
        }

        // Check session type
        match std::env::var("XDG_SESSION_TYPE") {
            Ok(session_type) if session_type == "wayland" => {
                debug!("Confirmed Wayland session type");
            },
            Ok(other) => {
                warn!("Session type is '{}', expected 'wayland'", other);
            },
            Err(_) => {
                warn!("XDG_SESSION_TYPE not set");
            },
        }

        Ok(())
    }

    /// Check permissions on the niri socket
    fn check_permissions(socket_path: &str) -> Result<()> {
        debug!("Checking permissions for niri socket");

        match std::fs::metadata(socket_path) {
            Ok(metadata) => {
                use std::os::unix::fs::MetadataExt;
                let mode = metadata.mode();
                debug!("Socket permissions: {:o}", mode);

                // Check if socket is readable and writable by the user
                let is_readable = mode & 0o400 != 0;
                let is_writable = mode & 0o200 != 0;

                if !is_readable || !is_writable {
                    return Err(NiriSpacerError::SocketConnection(std::io::Error::new(
                        std::io::ErrorKind::PermissionDenied,
                        "Insufficient permissions on niri socket",
                    )));
                }

                Ok(())
            },
            Err(e) => Err(NiriSpacerError::SocketConnection(e)),
        }
    }
}

/// Information about the current niri session
#[derive(Debug, Clone)]
pub struct NiriSessionInfo {
    pub socket_path: String,
    pub wayland_display: Option<String>,
    pub session_type: Option<String>,
    pub session_desktop: Option<String>,
    pub current_desktop: Option<String>,
    pub is_wayland: bool,
}

impl NiriSessionInfo {
    /// Get a human-readable summary of the session
    pub fn summary(&self) -> String {
        format!(
            "socket={}, wayland={}, type={:?}, desktop={:?}",
            self.socket_path,
            self.wayland_display.as_deref().unwrap_or("none"),
            self.session_type,
            self.current_desktop
        )
    }

    /// Check if this appears to be a proper niri session
    pub fn is_valid_niri_session(&self) -> bool {
        // Basic validation - has socket and is wayland
        Path::new(&self.socket_path).exists() && self.is_wayland
    }

    /// Get recommended configuration based on session info
    pub fn get_recommendations(&self) -> Vec<String> {
        let mut recommendations = Vec::new();

        if !self.is_wayland {
            recommendations.push(
                "Consider switching to a Wayland session for better niri compatibility".to_string(),
            );
        }

        if self.session_desktop.as_deref() != Some("niri")
            && self.current_desktop.as_deref() != Some("niri")
        {
            recommendations.push(
                "XDG desktop variables don't indicate niri - this may be expected".to_string(),
            );
        }

        if self.wayland_display.is_none() {
            recommendations.push(
                "WAYLAND_DISPLAY not set - this may cause issues with some applications"
                    .to_string(),
            );
        }

        if recommendations.is_empty() {
            recommendations.push("Session configuration looks good".to_string());
        }

        recommendations
    }
}
