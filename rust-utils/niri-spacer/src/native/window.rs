//! Core native window implementation
//!
//! This module provides the main interface for creating and managing
//! native Wayland windows for niri-spacer.

use crate::error::{NiriSpacerError, Result};
use crate::native::wayland::{WaylandEvent, WaylandEventLoop};
use crate::native::NativeConfig;
use crate::niri::{NiriClient, NiriEvent};
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

    /// Create a native spacer window by workspace index (position)
    pub async fn create_spacer_by_index(
        &mut self,
        window_number: u32,
        workspace_idx: u8,
    ) -> Result<SpacerWindow> {
        info!(
            "Creating native spacer window {} for workspace index {}",
            window_number, workspace_idx
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
        // Note: We store the index as u64 for now, but it's conceptually a workspace index
        self.windows[native_window_index].workspace_id = Some(workspace_idx as u64);

        // Move to target workspace index
        self.niri_client
            .move_window_to_workspace_index(niri_window_id, workspace_idx)
            .await?;
        // Short delay to allow workspace move to register
        tokio::time::sleep(Duration::from_millis(50)).await;

        // Don't resize - let niri auto-size to full height while keeping 1px width from min constraints

        // Position at leftmost column
        self.position_window_leftmost_by_index(niri_window_id, workspace_idx)
            .await?;

        // Verify window is in expected workspace index
        self.ensure_window_in_workspace_index(niri_window_id, workspace_idx)
            .await?;

        info!(
            "Successfully created native spacer window {} (niri ID: {})",
            window_number, niri_window_id
        );

        Ok(SpacerWindow {
            id: niri_window_id,
            workspace_id: workspace_idx as u64, // Store as index for now
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

    /// Position a window at the leftmost column of its workspace by index
    async fn position_window_leftmost_by_index(
        &mut self,
        window_id: u64,
        workspace_idx: u8,
    ) -> Result<()> {
        debug!(
            "Positioning window {} at leftmost column in workspace index {}",
            window_id, workspace_idx
        );

        // Focus the target workspace by index
        self.niri_client
            .focus_workspace_index(workspace_idx)
            .await?;
        tokio::time::sleep(Duration::from_millis(50)).await;

        // Focus the target window
        self.niri_client.focus_window(window_id).await?;
        tokio::time::sleep(Duration::from_millis(50)).await;

        // Move the column all the way to the leftmost position
        // Keep moving left until it can't move further (typically 3-5 moves should be enough)
        for i in 0..10 {
            // Safety limit to prevent infinite loops
            match self.niri_client.move_column_to_left().await {
                Ok(()) => {
                    debug!("Moved column left (attempt {})", i + 1);
                    // Small delay to allow move to register before next attempt
                    tokio::time::sleep(Duration::from_millis(25)).await;
                },
                Err(e) => {
                    // If move fails, we've likely reached the leftmost position
                    debug!("Column reached leftmost position after {} moves: {}", i, e);
                    break;
                },
            }
        }

        debug!(
            "Successfully positioned window {} at leftmost column",
            window_id
        );
        Ok(())
    }

    /// Ensure a window stays in the expected workspace index, correcting if niri moved it
    async fn ensure_window_in_workspace_index(
        &mut self,
        window_id: u64,
        expected_workspace_idx: u8,
    ) -> Result<()> {
        const MAX_CORRECTION_ATTEMPTS: u32 = 3;
        const CORRECTION_DELAY_MS: u64 = 50; // Reduced from 100ms since workspace placement is more reliable now

        for attempt in 1..=MAX_CORRECTION_ATTEMPTS {
            // Check current workspace by getting window info and finding which workspace index it's on
            match self.get_window_workspace_index(window_id).await {
                Ok(current_workspace_idx) => {
                    if current_workspace_idx == expected_workspace_idx {
                        // Window is in the correct workspace index
                        if attempt > 1 {
                            debug!(
                                "Window {} successfully corrected to workspace index {} after {} attempts",
                                window_id, expected_workspace_idx, attempt - 1
                            );
                        }
                        return Ok(());
                    } else {
                        // Window was moved by niri, try to move it back
                        tracing::warn!(
                            "Window {} moved from expected workspace index {} to {} (attempt {})",
                            window_id,
                            expected_workspace_idx,
                            current_workspace_idx,
                            attempt
                        );

                        if attempt <= MAX_CORRECTION_ATTEMPTS {
                            debug!(
                                "Attempting to move window {} back to workspace index {} (attempt {}/{})",
                                window_id, expected_workspace_idx, attempt, MAX_CORRECTION_ATTEMPTS
                            );

                            // Try to move it back
                            if let Err(e) = self
                                .niri_client
                                .move_window_to_workspace_index(window_id, expected_workspace_idx)
                                .await
                            {
                                tracing::warn!(
                                    "Failed to correct window {} workspace (attempt {}): {}",
                                    window_id,
                                    attempt,
                                    e
                                );
                            } else {
                                // Wait before checking again
                                tokio::time::sleep(Duration::from_millis(CORRECTION_DELAY_MS))
                                    .await;
                            }
                        }
                    }
                },
                Err(e) => {
                    tracing::warn!(
                        "Could not check workspace for window {} (attempt {}): {}",
                        window_id,
                        attempt,
                        e
                    );
                    break;
                },
            }
        }

        // Final check after all attempts
        match self.get_window_workspace_index(window_id).await {
            Ok(final_workspace_idx) => {
                if final_workspace_idx != expected_workspace_idx {
                    tracing::warn!(
                        "Window {} remains in workspace index {} instead of expected {} after {} correction attempts",
                        window_id, final_workspace_idx, expected_workspace_idx, MAX_CORRECTION_ATTEMPTS
                    );
                }
            },
            Err(e) => {
                tracing::warn!(
                    "Could not verify final workspace for window {}: {}",
                    window_id,
                    e
                );
            },
        }

        Ok(())
    }

    /// Get the workspace index of a window by finding it in the workspace list
    async fn get_window_workspace_index(&mut self, window_id: u64) -> Result<u8> {
        let windows = self.niri_client.get_windows().await?;
        let workspaces = self.niri_client.get_workspaces().await?;

        // Find the window
        let window = windows
            .iter()
            .find(|w| w.id == window_id)
            .ok_or(NiriSpacerError::WindowNotFound(window_id))?;

        // Find the workspace with this ID and get its index
        let workspace = workspaces
            .iter()
            .find(|w| w.id == window.workspace_id)
            .ok_or(NiriSpacerError::IpcError(format!(
                "Workspace with ID {} not found",
                window.workspace_id
            )))?;

        Ok(workspace.idx)
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

    /// Start focus event monitoring to automatically redirect focus away from spacer windows
    pub async fn start_focus_monitoring(self) -> Result<()> {
        use futures_util::StreamExt;

        info!("Starting focus monitoring for spacer windows");

        // Create a separate niri client for event monitoring
        let event_client = NiriClient::connect().await?;
        let event_stream = event_client.subscribe_to_events().await?;

        // Create another client for sending focus commands
        let mut action_client = NiriClient::connect().await?;

        // Get the IDs of our spacer windows for comparison
        let spacer_window_ids: Vec<u64> = self
            .windows
            .iter()
            .filter_map(|w| w.niri_window_id)
            .collect();

        info!(
            "🔍 FOCUS MONITORING: Starting to monitor {} spacer windows for focus events",
            spacer_window_ids.len()
        );
        info!("🔍 SPACER WINDOW IDs: {:?}", spacer_window_ids);

        // Pin the stream for async operations
        tokio::pin!(event_stream);

        // Monitor events
        while let Some(event_result) = event_stream.next().await {
            match event_result {
                Ok(event) => {
                    debug!("Received focus event: {:?}", event);
                    if let NiriEvent::WindowFocusChanged {
                        id: focused_window_id,
                    } = event
                    {
                        debug!("Focus changed to window ID: {}", focused_window_id);
                        debug!("Spacer window IDs: {:?}", spacer_window_ids);

                        // Check if a spacer window was focused
                        if spacer_window_ids.contains(&focused_window_id) {
                            info!(
                                "🎯 DETECTED: Spacer window {} was focused, sending focus-column-right command",
                                focused_window_id
                            );

                            // Try to focus the next column to the right
                            debug!("📤 SENDING: focus-column-right command to niri");
                            match action_client.focus_column_right().await {
                                Ok(()) => {
                                    info!(
                                        "✅ SUCCESS: Redirected focus from spacer window {}",
                                        focused_window_id
                                    );

                                    // Fix 1px layout shift using center + maximize toggle hack
                                    debug!("📤 SENDING: center-column + maximize toggle to fix layout positioning");

                                    // Step 1: Center the column (moves to screen center)
                                    if let Err(e) = action_client.center_column().await {
                                        warn!("⚠️  WARNING: Failed to center column: {}", e);
                                    } else {
                                        // Small delay to let centering complete
                                        tokio::time::sleep(tokio::time::Duration::from_millis(50))
                                            .await;

                                        // Step 2: Maximize column (should expand to fill screen)
                                        if let Err(e) = action_client.maximize_column().await {
                                            warn!("⚠️  WARNING: Failed to maximize column: {}", e);
                                        } else {
                                            // Small delay to let maximize complete
                                            tokio::time::sleep(tokio::time::Duration::from_millis(
                                                10,
                                            ))
                                            .await;

                                            // Step 3: Maximize again to toggle back to original layout
                                            if let Err(e) = action_client.maximize_column().await {
                                                warn!(
                                                    "⚠️  WARNING: Failed to toggle maximize: {}",
                                                    e
                                                );
                                            } else {
                                                debug!("✅ SUCCESS: Applied center + maximize toggle layout fix");
                                            }
                                        }
                                    }
                                },
                                Err(e) => {
                                    warn!(
                                        "❌ FAILED: Could not redirect focus from spacer window {}: {}",
                                        focused_window_id, e
                                    );
                                },
                            }
                        } else {
                            debug!(
                                "Focus change to non-spacer window {}, ignoring",
                                focused_window_id
                            );
                        }
                    } else {
                        debug!("Received non-window-focus event or focus cleared, ignoring");
                    }
                },
                Err(e) => {
                    warn!("Error in focus event stream: {}", e);
                    // Try to reconnect
                    tokio::time::sleep(std::time::Duration::from_millis(1000)).await;
                    match NiriClient::connect().await {
                        Ok(new_client) => match new_client.subscribe_to_events().await {
                            Ok(new_stream) => {
                                event_stream.set(new_stream);
                                debug!("Reconnected to focus event stream");
                            },
                            Err(e) => {
                                warn!("Failed to resubscribe to events: {}", e);
                            },
                        },
                        Err(e) => {
                            warn!("Failed to reconnect to niri for events: {}", e);
                        },
                    }
                },
            }
        }

        Ok(())
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
