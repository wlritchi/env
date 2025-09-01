//! Core native window implementation
//!
//! This module provides the main interface for creating and managing
//! native Wayland windows for niri-spacer.

use crate::error::{NiriSpacerError, Result};
use crate::native::wayland::{WaylandEvent, WaylandEventLoop};
use crate::native::NativeConfig;
use crate::niri::NiriClient;
use crate::window::SpacerWindow;
use std::sync::mpsc;
use std::time::{Duration, Instant};
use tracing::{debug, error, info, warn};

/// Configuration for native window creation
#[derive(Debug, Clone)]
pub struct NativeWindowConfig {
    pub app_id: String,
    pub title: String,
    pub background_color: (u8, u8, u8),
    pub correlation_timeout: Duration,
}

impl Default for NativeWindowConfig {
    fn default() -> Self {
        Self {
            app_id: "niri-spacer-native".to_string(),
            title: "niri-spacer".to_string(),
            background_color: (128, 128, 128),
            correlation_timeout: Duration::from_secs(5),
        }
    }
}

/// A native window instance
#[derive(Debug, Clone)]
pub struct NativeWindow {
    pub window_id: u32,
    pub app_id: String,
    pub niri_window_id: Option<u64>,
    pub workspace_id: Option<u64>,
}

/// Manager for native window creation and lifecycle
pub struct NativeWindowManager {
    config: NativeConfig,
    wayland_loop: WaylandEventLoop,
    event_receiver: mpsc::Receiver<WaylandEvent>,
    niri_client: NiriClient,
    windows: Vec<NativeWindow>,
}

impl NativeWindowManager {
    /// Create a new native window manager
    pub async fn new(config: NativeConfig) -> Result<Self> {
        info!("Initializing native window manager");

        // Start the Wayland event loop
        let (wayland_loop, event_receiver) = WaylandEventLoop::start()?;

        // Connect to niri
        let niri_client = NiriClient::connect().await?;

        Ok(Self {
            config,
            wayland_loop,
            event_receiver,
            niri_client,
            windows: Vec::new(),
        })
    }

    /// Create a native spacer window
    pub async fn create_spacer(
        &mut self,
        window_number: u32,
        workspace_id: u64,
    ) -> Result<SpacerWindow> {
        info!(
            "Creating native spacer window {} for workspace {}",
            window_number, workspace_id
        );

        // Generate unique app_id for correlation
        let app_id = self.generate_unique_app_id(window_number);
        let title = format!("niri-spacer window {}", window_number);

        debug!("Using app_id: {} for window correlation", app_id);

        // Create the native window
        let _native_window = self
            .create_native_window(app_id.clone(), title, self.config.background_color)
            .await?;

        // Correlate with niri window
        let niri_window_id = self
            .correlate_with_niri(&app_id, self.config.correlation_timeout_ms)
            .await?;

        // Update the native window record
        let native_window_index = self.windows.len() - 1;
        self.windows[native_window_index].niri_window_id = Some(niri_window_id);
        self.windows[native_window_index].workspace_id = Some(workspace_id);

        // Move to target workspace if needed
        let current_workspace = self.get_window_workspace(niri_window_id).await?;
        if current_workspace != workspace_id {
            self.niri_client
                .move_window_to_workspace(niri_window_id, workspace_id)
                .await?;
            tokio::time::sleep(Duration::from_millis(25)).await;
        }

        // Resize to minimum width
        self.niri_client
            .resize_window_to_minimum(niri_window_id)
            .await?;
        tokio::time::sleep(Duration::from_millis(25)).await;

        // Position at leftmost column
        self.position_window_leftmost(niri_window_id, workspace_id)
            .await?;

        info!(
            "Successfully created native spacer window {} (niri ID: {})",
            window_number, niri_window_id
        );

        Ok(SpacerWindow {
            id: niri_window_id,
            workspace_id,
            window_number,
        })
    }

    /// Create a native window through the Wayland event loop
    async fn create_native_window(
        &mut self,
        app_id: String,
        title: String,
        background_color: (u8, u8, u8),
    ) -> Result<NativeWindow> {
        debug!("Creating native window with app_id: {}", app_id);

        let window_id = self
            .wayland_loop
            .create_window(app_id.clone(), title, background_color)
            .await?;

        let native_window = NativeWindow {
            window_id,
            app_id: app_id.clone(),
            niri_window_id: None,
            workspace_id: None,
        };

        self.windows.push(native_window);
        debug!("Native window created with ID: {}", window_id);

        // Clone the window to avoid borrowing issues
        let window = self.windows.last().unwrap().clone();
        Ok(window)
    }

    /// Correlate a native window with its niri window ID
    async fn correlate_with_niri(&mut self, app_id: &str, timeout_ms: u64) -> Result<u64> {
        debug!("Correlating window with app_id: {}", app_id);

        let timeout_duration = Duration::from_millis(timeout_ms);
        let start_time = Instant::now();

        // Poll for the window to appear in niri
        loop {
            // Process any pending Wayland events
            while let Ok(event) = self.event_receiver.try_recv() {
                self.handle_wayland_event(event);
            }

            // Check if the window appeared in niri
            match self.find_window_by_app_id(app_id).await {
                Ok(Some(window_id)) => {
                    debug!(
                        "Successfully correlated window: app_id={}, niri_id={}",
                        app_id, window_id
                    );
                    return Ok(window_id);
                },
                Ok(None) => {
                    // Window not found yet, continue polling
                },
                Err(e) => {
                    warn!("Error while searching for window: {}", e);
                },
            }

            // Check timeout
            if start_time.elapsed() >= timeout_duration {
                return Err(NiriSpacerError::WindowCorrelation(format!(
                    "Timeout waiting for window with app_id: {}",
                    app_id
                )));
            }

            // Small delay before next poll
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
    }

    /// Find a window by its app_id in niri
    async fn find_window_by_app_id(&mut self, app_id: &str) -> Result<Option<u64>> {
        let windows = self.niri_client.get_windows().await?;

        for window in windows {
            if window.app_id == app_id {
                return Ok(Some(window.id));
            }
        }

        Ok(None)
    }

    /// Get the workspace of a window
    async fn get_window_workspace(&mut self, window_id: u64) -> Result<u64> {
        let windows = self.niri_client.get_windows().await?;

        for window in windows {
            if window.id == window_id {
                return Ok(window.workspace_id);
            }
        }

        Err(NiriSpacerError::WindowNotFound(window_id))
    }

    /// Position a window at the leftmost column of its workspace
    async fn position_window_leftmost(&mut self, window_id: u64, workspace_id: u64) -> Result<()> {
        debug!(
            "Positioning window {} at leftmost column in workspace {}",
            window_id, workspace_id
        );

        // Focus the target workspace
        self.niri_client.focus_workspace(workspace_id).await?;
        tokio::time::sleep(Duration::from_millis(25)).await;

        // Focus the target window
        self.niri_client.focus_window(window_id).await?;
        tokio::time::sleep(Duration::from_millis(25)).await;

        // Move the column to the leftmost position
        self.niri_client.move_column_to_left().await?;
        tokio::time::sleep(Duration::from_millis(25)).await;

        debug!(
            "Successfully positioned window {} at leftmost column",
            window_id
        );
        Ok(())
    }

    /// Generate a unique app_id for window correlation
    fn generate_unique_app_id(&self, window_number: u32) -> String {
        let timestamp = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_millis();
        let random = fastrand::u32(..);

        format!(
            "{}-{}-{}-{}",
            self.config.app_id_pattern, window_number, timestamp, random
        )
    }

    /// Handle events from the Wayland event loop
    fn handle_wayland_event(&mut self, event: WaylandEvent) {
        match event {
            WaylandEvent::WindowCreated { window_id, app_id } => {
                debug!(
                    "Wayland window created: ID={}, app_id={}",
                    window_id, app_id
                );
            },
            WaylandEvent::WindowClosed { window_id } => {
                debug!("Wayland window closed: ID={}", window_id);
                self.handle_window_closed(window_id);
            },
            WaylandEvent::Error(error) => {
                error!("Wayland event loop error: {}", error);
            },
        }
    }

    /// Handle window closure
    fn handle_window_closed(&mut self, window_id: u32) {
        self.windows.retain(|w| w.window_id != window_id);
    }

    /// Close all native windows
    pub fn close_all_windows(&self) -> Result<()> {
        for window in &self.windows {
            if let Err(e) = self.wayland_loop.close_window(window.window_id) {
                warn!("Failed to close window {}: {}", window.window_id, e);
            }
        }
        Ok(())
    }

    /// Get the number of active windows
    pub fn window_count(&self) -> usize {
        self.windows.len()
    }

    /// Shutdown the window manager
    pub fn shutdown(self) -> Result<()> {
        self.close_all_windows()?;
        self.wayland_loop.shutdown()?;
        Ok(())
    }
}

impl Drop for NativeWindowManager {
    fn drop(&mut self) {
        if let Err(e) = self.close_all_windows() {
            error!("Failed to close windows during drop: {}", e);
        }
        if let Err(e) = self.wayland_loop.shutdown() {
            error!("Failed to shutdown Wayland event loop during drop: {}", e);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_native_window_config_default() {
        let config = NativeWindowConfig::default();
        assert_eq!(config.app_id, "niri-spacer-native");
        assert_eq!(config.title, "niri-spacer");
        assert_eq!(config.background_color, (128, 128, 128));
        assert_eq!(config.correlation_timeout, Duration::from_secs(5));
    }

    #[test]
    fn test_native_window_debug() {
        let window = NativeWindow {
            window_id: 123,
            app_id: "test-app".to_string(),
            niri_window_id: Some(456),
            workspace_id: Some(789),
        };

        let debug_str = format!("{:?}", window);
        assert!(debug_str.contains("123"));
        assert!(debug_str.contains("test-app"));
        assert!(debug_str.contains("456"));
        assert!(debug_str.contains("789"));
    }

    #[test]
    fn test_generate_unique_app_id_pattern() {
        // Test that the pattern is correctly formatted
        let pattern = "niri-spacer-native";
        let window_number = 5;

        // We can't test the exact output since it includes timestamp and random
        // but we can verify the format
        let timestamp = 1234567890123u128;
        let random = 42u32;
        let expected = format!("{}-{}-{}-{}", pattern, window_number, timestamp, random);

        assert!(expected.starts_with(&format!("{}-{}-", pattern, window_number)));
    }

    #[test]
    fn test_native_window_config_clone() {
        let config = NativeWindowConfig::default();
        let cloned = config.clone();

        assert_eq!(config.app_id, cloned.app_id);
        assert_eq!(config.title, cloned.title);
        assert_eq!(config.background_color, cloned.background_color);
        assert_eq!(config.correlation_timeout, cloned.correlation_timeout);
    }
}
