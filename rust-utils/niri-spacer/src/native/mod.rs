//! Native Wayland window implementation for niri-spacer
//!
//! This module provides native window creation using smithay-client-toolkit
//! and softbuffer, offering better performance and more direct control
//! compared to spawning terminal processes.

use crate::error::{NiriSpacerError, Result};
use crate::window::SpacerWindow;

pub mod surface;
pub mod wayland;
pub mod window;

pub use surface::SurfaceManager;
pub use wayland::WaylandEventLoop;
pub use window::{NativeWindow, NativeWindowConfig, NativeWindowManager};

/// Configuration for native window behavior
#[derive(Debug, Clone)]
pub struct NativeConfig {
    /// Background color for native windows (RGB)
    pub background_color: (u8, u8, u8),
    /// Timeout for window correlation (milliseconds)
    pub correlation_timeout_ms: u64,
    /// App ID pattern for window correlation
    pub app_id_pattern: String,
    /// Whether to enable debug logging for native windows
    pub debug_native: bool,
}

impl Default for NativeConfig {
    fn default() -> Self {
        Self {
            background_color: (128, 128, 128), // Gray
            correlation_timeout_ms: 5000,
            app_id_pattern: "niri-spacer-native".to_string(),
            debug_native: false,
        }
    }
}

/// Check if native windows are supported on this system
pub fn is_native_supported() -> bool {
    // Check for Wayland environment
    std::env::var("WAYLAND_DISPLAY").is_ok()
        || std::env::var("XDG_SESSION_TYPE")
            .map(|t| t == "wayland")
            .unwrap_or(false)
}

/// Create a native window manager with the given configuration
pub async fn create_native_manager(config: NativeConfig) -> Result<NativeWindowManager> {
    if !is_native_supported() {
        return Err(NiriSpacerError::NativeNotSupported);
    }

    NativeWindowManager::new(config).await
}

/// Create a native spacer window
pub async fn create_spacer_with_strategy(
    config: &NativeConfig,
    window_number: u32,
    workspace_id: u64,
) -> Result<SpacerWindow> {
    let mut manager = create_native_manager(config.clone()).await?;
    manager.create_spacer(window_number, workspace_id).await
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_default_config() {
        let config = NativeConfig::default();
        assert_eq!(config.background_color, (128, 128, 128));
        assert_eq!(config.correlation_timeout_ms, 5000);
        assert_eq!(config.app_id_pattern, "niri-spacer-native");
        assert!(!config.debug_native);
    }

    #[test]
    fn test_is_native_supported() {
        // This test just ensures the function doesn't panic
        // The actual support depends on the environment
        let _ = is_native_supported();
    }

    #[test]
    fn test_config_clone() {
        let config = NativeConfig::default();
        let cloned = config.clone();
        assert_eq!(config.background_color, cloned.background_color);
        assert_eq!(config.app_id_pattern, cloned.app_id_pattern);
    }
}
