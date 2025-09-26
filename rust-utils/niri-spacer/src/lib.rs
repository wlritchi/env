//! niri-spacer: A utility to spawn placeholder windows in niri workspaces
//!
//! This library provides functionality to create and manage "spacer" windows
//! in the niri Wayland compositor to improve tiling behavior and maintain
//! consistent workspace layouts.

pub mod error;
pub mod native;
pub mod niri;
pub mod session;
pub mod signal;
pub mod window;
pub mod workspace;

// Re-export commonly used types
pub use error::{NiriSpacerError, Result};
pub use native::{create_native_manager, is_native_supported, NativeConfig};
pub use niri::{NiriClient, NiriRequest, NiriResponse, Window, Workspace};
pub use session::{NiriSessionInfo, SessionValidator};
pub use window::{SpacerWindow, WindowManager};
pub use workspace::{WorkspaceManager, WorkspaceStats};

/// Application metadata
pub const APP_NAME: &str = "niri-spacer";
pub const APP_VERSION: &str = env!("CARGO_PKG_VERSION");
pub const APP_DESCRIPTION: &str = env!("CARGO_PKG_DESCRIPTION");

/// Default configuration values
pub mod defaults {
    /// Default number of spacer windows to create
    pub const DEFAULT_WINDOW_COUNT: u32 = 9;

    /// Minimum allowed window count
    pub const MIN_WINDOW_COUNT: u32 = 1;

    /// Maximum allowed window count
    pub const MAX_WINDOW_COUNT: u32 = 50;

    /// Default delay between window spawns (milliseconds)
    pub const DEFAULT_SPAWN_DELAY_MS: u64 = 50;

    /// Default delay between IPC operations (milliseconds)
    pub const DEFAULT_OPERATION_DELAY_MS: u64 = 25;
}

/// Core application logic for niri-spacer
pub struct NiriSpacer {
    session_info: NiriSessionInfo,
    window_manager: WindowManager,
    workspace_manager: WorkspaceManager,
    active_spacers: Vec<SpacerWindow>,
    native_manager: Option<native::NativeWindowManager>,
}

impl NiriSpacer {
    /// Initialize niri-spacer with environment validation and default window manager
    pub async fn new() -> Result<Self> {
        // Validate environment and detect session
        let session_info = SessionValidator::validate_environment()?;

        // Initialize managers
        let window_manager = WindowManager::new().await?;
        let workspace_manager = WorkspaceManager::new().await?;

        Ok(Self {
            session_info,
            window_manager,
            workspace_manager,
            active_spacers: Vec::new(),
            native_manager: None,
        })
    }

    /// Initialize niri-spacer with custom native configuration
    pub async fn new_with_native_config(native_config: NativeConfig) -> Result<Self> {
        // Validate environment and detect session
        let session_info = SessionValidator::validate_environment_native_only()?;

        // Initialize managers with custom configuration
        let window_manager = WindowManager::new_with_native_config(native_config.clone()).await?;
        let workspace_manager = WorkspaceManager::new().await?;

        // Create and store native manager for persistent mode
        let native_manager = Some(native::create_native_manager(native_config).await?);

        Ok(Self {
            session_info,
            window_manager,
            workspace_manager,
            active_spacers: Vec::new(),
            native_manager,
        })
    }

    /// Run the main spacer creation process
    pub async fn run(&mut self, window_count: u32) -> Result<Vec<SpacerWindow>> {
        // Validate window count
        if !(defaults::MIN_WINDOW_COUNT..=defaults::MAX_WINDOW_COUNT).contains(&window_count) {
            return Err(NiriSpacerError::InvalidWindowCount(window_count));
        }

        // Get workspace statistics before starting
        let initial_stats = self.workspace_manager.get_workspace_stats().await?;

        // Find optimal starting workspace index
        let starting_workspace_idx = self
            .workspace_manager
            .suggest_starting_workspace(window_count)
            .await?;

        // Validate workspace availability
        self.workspace_manager
            .validate_workspace_availability(starting_workspace_idx, window_count)
            .await?;

        // Create the spacer windows
        let spacers = self
            .create_spacer_batch_persistent(window_count, starting_workspace_idx)
            .await?;

        // Validate the results
        self.window_manager.validate_spacers(&spacers).await?;

        // Get final statistics
        let final_stats = self.workspace_manager.get_workspace_stats().await?;

        // Store active spacers
        self.active_spacers.extend_from_slice(&spacers);

        // Log the results
        tracing::info!(
            "Created {} spacer windows in workspace indices {}-{}",
            spacers.len(),
            starting_workspace_idx,
            starting_workspace_idx + window_count as u8 - 1
        );
        tracing::info!("Before: {}", initial_stats.summary());
        tracing::info!("After: {}", final_stats.summary());

        Ok(spacers)
    }

    /// Get session information
    pub fn session_info(&self) -> &NiriSessionInfo {
        &self.session_info
    }

    /// Get current workspace statistics
    pub async fn get_stats(&mut self) -> Result<WorkspaceStats> {
        self.workspace_manager.get_workspace_stats().await
    }

    /// Get list of active spacer windows
    pub fn get_active_spacers(&self) -> &[SpacerWindow] {
        &self.active_spacers
    }

    /// Start focus monitoring to automatically redirect focus away from spacer windows
    pub async fn start_focus_monitoring(&mut self) -> Result<()> {
        if self.native_manager.is_some() {
            tracing::info!("Starting focus event monitoring for spacer windows");

            // Clone the spacer window IDs for the background task
            let spacer_window_ids: Vec<u64> = self.active_spacers.iter().map(|s| s.id).collect();

            // Start focus monitoring in a background task without consuming the native_manager
            let focus_task = tokio::spawn(async move {
                if let Err(e) = Self::run_focus_monitoring(spacer_window_ids).await {
                    tracing::error!("Focus monitoring failed: {}", e);
                }
            });

            // Store the task handle (could be stored in struct if we need to manage it)
            drop(focus_task);

            tracing::info!("Focus monitoring started");
            Ok(())
        } else {
            Err(NiriSpacerError::NativeNotSupported)
        }
    }

    /// Run focus monitoring without consuming the native manager
    async fn run_focus_monitoring(spacer_window_ids: Vec<u64>) -> Result<()> {
        use futures_util::StreamExt;

        tracing::info!(
            "🔍 FOCUS MONITORING: Starting to monitor {} spacer windows for focus events",
            spacer_window_ids.len()
        );
        tracing::info!("🔍 SPACER WINDOW IDs: {:?}", spacer_window_ids);

        // Create a niri client for event monitoring
        let event_client = crate::niri::NiriClient::connect().await?;
        let event_stream = event_client.subscribe_to_events().await?;

        // Create another client for sending focus commands
        let mut action_client = crate::niri::NiriClient::connect().await?;

        // Pin the stream for async operations
        tokio::pin!(event_stream);

        // Monitor events
        while let Some(event_result) = event_stream.next().await {
            match event_result {
                Ok(event) => {
                    tracing::debug!("Received focus event: {:?}", event);
                    if let crate::niri::NiriEvent::WindowFocusChanged {
                        id: focused_window_id,
                    } = event
                    {
                        tracing::debug!("Focus changed to window ID: {}", focused_window_id);

                        // Check if a spacer window was focused
                        if spacer_window_ids.contains(&focused_window_id) {
                            tracing::info!(
                                "🎯 DETECTED: Spacer window {} was focused, checking position and redirecting focus",
                                focused_window_id
                            );

                            // First, check and fix position if needed (before redirecting focus)
                            if let Err(e) =
                                Self::check_and_fix_single_spacer_position(focused_window_id).await
                            {
                                tracing::warn!(
                                    "Failed to check/fix position for spacer window {}: {}",
                                    focused_window_id,
                                    e
                                );
                            }

                            // Then redirect focus away from the spacer window
                            match action_client.focus_column_right().await {
                                Ok(()) => {
                                    tracing::info!(
                                        "✅ SUCCESS: Redirected focus from spacer window {}",
                                        focused_window_id
                                    );

                                    // Apply realign strategy if workspace has enough windows
                                    match Self::count_workspace_windows(
                                        &mut action_client,
                                        focused_window_id,
                                    )
                                    .await
                                    {
                                        Ok(window_count) => {
                                            if window_count < 3 {
                                                tracing::debug!(
                                                    "Only {} windows in workspace, skipping layout fix to avoid focus loops",
                                                    window_count
                                                );
                                            } else {
                                                tracing::debug!(
                                                    "Found {} windows in workspace, applying layout fix (right→left)",
                                                    window_count
                                                );

                                                // Try focus-shift fix first; fallback to maximize-toggle
                                                if let Err(e) = Self::apply_focus_shift_layout_fix(
                                                    &mut action_client,
                                                )
                                                .await
                                                {
                                                    tracing::warn!(
                                                        "Focus-shift layout fix failed: {}, trying maximize toggle hack",
                                                        e
                                                    );
                                                    if let Err(e2) =
                                                        Self::apply_maximize_toggle_layout_fix(
                                                            &mut action_client,
                                                        )
                                                        .await
                                                    {
                                                        tracing::warn!(
                                                            "Both layout fixes failed: focus-shift({}), maximize-toggle({})",
                                                            e, e2
                                                        );
                                                    }
                                                }
                                            }
                                        },
                                        Err(e) => {
                                            tracing::warn!(
                                                "Could not count workspace windows: {}, skipping layout fix",
                                                e
                                            );
                                        },
                                    }
                                },
                                Err(e) => {
                                    tracing::warn!(
                                        "❌ FAILED: Could not redirect focus from spacer window {}: {}",
                                        focused_window_id, e
                                    );
                                },
                            }
                        } else {
                            tracing::debug!(
                                "Focus change to non-spacer window {}, ignoring",
                                focused_window_id
                            );
                        }
                    }
                },
                Err(e) => {
                    tracing::warn!("Error in focus event stream: {}", e);
                    // Try to reconnect
                    tokio::time::sleep(std::time::Duration::from_millis(1000)).await;
                    match crate::niri::NiriClient::connect().await {
                        Ok(new_client) => match new_client.subscribe_to_events().await {
                            Ok(new_stream) => {
                                event_stream.set(new_stream);
                                tracing::debug!("Reconnected to focus event stream");
                            },
                            Err(e) => {
                                tracing::warn!("Failed to resubscribe to events: {}", e);
                            },
                        },
                        Err(e) => {
                            tracing::warn!("Failed to reconnect to niri for events: {}", e);
                        },
                    }
                },
            }
        }

        Ok(())
    }

    /// Count the number of windows in the workspace containing the given window
    async fn count_workspace_windows(
        action_client: &mut crate::niri::NiriClient,
        window_id: u64,
    ) -> Result<usize> {
        let windows = action_client.get_windows().await?;

        // Find the workspace ID of the given window
        let target_workspace_id = windows
            .iter()
            .find(|w| w.id == window_id)
            .map(|w| w.workspace_id)
            .ok_or(NiriSpacerError::WindowNotFound(window_id))?;

        // Count windows in that workspace
        let window_count = windows
            .iter()
            .filter(|w| w.workspace_id == target_workspace_id)
            .count();

        Ok(window_count)
    }

    /// Apply focus-shift layout fix: move focus right then left to reset positioning
    async fn apply_focus_shift_layout_fix(
        action_client: &mut crate::niri::NiriClient,
    ) -> Result<()> {
        tracing::debug!("📤 APPLYING: focus-shift layout fix (focus right → focus left)");

        // Move focus one more column to the right
        action_client.focus_column_right().await?;

        // Small delay to let focus change register
        tokio::time::sleep(std::time::Duration::from_millis(10)).await;

        // Move focus back to the left to original position
        match action_client.focus_column_left().await {
            Ok(()) => {
                tracing::debug!("✅ SUCCESS: Applied focus-shift layout fix");
                Ok(())
            },
            Err(e) => {
                tracing::warn!("❌ FAILED: Focus-shift layout fix failed: {}", e);
                Err(e)
            },
        }
    }

    /// Apply maximize toggle layout fix: center + double maximize to reset positioning
    async fn apply_maximize_toggle_layout_fix(
        action_client: &mut crate::niri::NiriClient,
    ) -> Result<()> {
        tracing::debug!("📤 APPLYING: maximize toggle layout fix (center + double maximize)");

        // Step 1: Center the column (moves to screen center)
        action_client.center_column().await?;

        // Small delay to let centering complete
        tokio::time::sleep(std::time::Duration::from_millis(50)).await;

        // Step 2: Maximize column (should expand to fill screen)
        action_client.maximize_column().await?;

        // Small delay to let maximize complete
        tokio::time::sleep(std::time::Duration::from_millis(10)).await;

        // Step 3: Maximize again to toggle back to original layout
        match action_client.maximize_column().await {
            Ok(()) => {
                tracing::debug!("✅ SUCCESS: Applied maximize toggle layout fix");
                Ok(())
            },
            Err(e) => {
                tracing::warn!("❌ FAILED: Maximize toggle layout fix failed: {}", e);
                Err(e)
            },
        }
    }

    /// Check and fix position of a single spacer window (used during focus events)
    async fn check_and_fix_single_spacer_position(window_id: u64) -> Result<()> {
        // Create niri client for window operations
        let mut niri_client = crate::niri::NiriClient::connect().await?;

        // Get window information
        let windows = niri_client.get_windows().await?;
        let window = windows
            .iter()
            .find(|w| w.id == window_id)
            .ok_or(NiriSpacerError::WindowNotFound(window_id))?;

        // Check if window is in the correct column position
        let is_in_leftmost_column = match &window.layout {
            Some(layout) => match layout.pos_in_scrolling_layout {
                Some((column_index, _tile_index)) => column_index == 1, // 1-based indexing
                None => {
                    tracing::debug!(
                        "Spacer window {} is floating, cannot position check",
                        window_id
                    );
                    return Ok(()); // Floating windows can't be positioned, skip
                },
            },
            None => {
                tracing::debug!(
                    "No layout info for spacer window {}, assuming correctly positioned",
                    window_id
                );
                return Ok(()); // Assume correct if no layout info
            },
        };

        if !is_in_leftmost_column {
            tracing::warn!(
                "🚨 IMMEDIATE FIX: Spacer window {} is mispositioned - not in leftmost column, fixing now",
                window_id
            );

            // Get workspace information for repositioning
            let workspaces = niri_client.get_workspaces().await?;
            let workspace_idx = workspaces
                .iter()
                .find(|w| w.id == window.workspace_id)
                .map(|w| w.idx)
                .ok_or_else(|| {
                    NiriSpacerError::IpcError(format!(
                        "Workspace with ID {} not found",
                        window.workspace_id
                    ))
                })?;

            // Reposition the window directly
            if let Err(e) = Self::reposition_single_spacer_direct(workspace_idx).await {
                tracing::error!(
                    "Failed to immediately reposition spacer window {}: {}",
                    window_id,
                    e
                );
                return Err(e);
            } else {
                tracing::info!(
                    "✅ IMMEDIATE SUCCESS: Repositioned spacer window {} to leftmost column",
                    window_id
                );
            }
        } else {
            tracing::debug!("Spacer window {} is correctly positioned", window_id);
        }

        Ok(())
    }

    /// Reposition a single spacer window directly (streamlined version for immediate fixes)
    async fn reposition_single_spacer_direct(workspace_idx: u8) -> Result<()> {
        let mut niri_client = crate::niri::NiriClient::connect().await?;

        // Remember the originally focused workspace to restore it later
        let workspaces = niri_client.get_workspaces().await?;
        let original_focused_workspace = workspaces.iter().find(|w| w.is_focused).map(|w| w.idx);

        tracing::debug!(
            "Immediate repositioning: spacer on workspace {} (original focus: {:?})",
            workspace_idx,
            original_focused_workspace
        );

        // Focus the target workspace (window is already focused, but we need the right workspace)
        if original_focused_workspace != Some(workspace_idx) {
            niri_client.focus_workspace_index(workspace_idx).await?;
            tokio::time::sleep(std::time::Duration::from_millis(25)).await; // Shorter delay for immediate response
        }

        // Move column to first position (window is already focused)
        match niri_client.move_column_to_first().await {
            Ok(()) => {
                tracing::debug!("Moved column to first position using move_column_to_first");
                tokio::time::sleep(std::time::Duration::from_millis(25)).await;
            },
            Err(e) => {
                // Fall back to the old method
                tracing::debug!(
                    "move_column_to_first failed ({}), falling back to move_column_to_left loop",
                    e
                );
                for i in 0..5 {
                    // Fewer attempts for immediate response
                    match niri_client.move_column_to_left().await {
                        Ok(()) => {
                            tracing::debug!("Moved column left (attempt {})", i + 1);
                            tokio::time::sleep(std::time::Duration::from_millis(10)).await;
                            // Shorter delay
                        },
                        Err(e) => {
                            tracing::debug!(
                                "Column reached leftmost position after {} moves: {}",
                                i,
                                e
                            );
                            break;
                        },
                    }
                }
            },
        }

        // Restore the original workspace focus if it was different
        if let Some(original_idx) = original_focused_workspace {
            if original_idx != workspace_idx {
                tracing::debug!("Restoring focus to original workspace {}", original_idx);
                match niri_client.focus_workspace_index(original_idx).await {
                    Ok(()) => {
                        tracing::debug!(
                            "Successfully restored focus to workspace {}",
                            original_idx
                        );
                    },
                    Err(e) => {
                        tracing::warn!(
                            "Failed to restore focus to original workspace {}: {}",
                            original_idx,
                            e
                        );
                    },
                }
            }
        }

        Ok(())
    }

    /// Clean up all active spacer windows and resources
    pub async fn cleanup(&mut self) -> Result<()> {
        tracing::info!(
            "Cleaning up {} active spacer windows",
            self.active_spacers.len()
        );

        // Shutdown native manager if present - this will close native windows
        if let Some(native_manager) = self.native_manager.take() {
            if let Err(e) = native_manager.shutdown() {
                tracing::warn!("Error shutting down native manager: {}", e);
            }
        }

        self.active_spacers.clear();
        tracing::info!("Cleanup completed");

        Ok(())
    }

    /// Create multiple spacer windows using the persistent native manager
    async fn create_spacer_batch_persistent(
        &mut self,
        window_count: u32,
        starting_workspace_idx: u8,
    ) -> Result<Vec<SpacerWindow>> {
        tracing::info!(
            "Creating batch of {} spacer windows starting from workspace index {} (persistent mode)",
            window_count,
            starting_workspace_idx
        );

        let mut spacers = Vec::with_capacity(window_count as usize);

        // Get mutable reference to native manager
        let native_manager = self
            .native_manager
            .as_mut()
            .ok_or(NiriSpacerError::NativeNotSupported)?;

        for i in 0..window_count {
            let window_number = i + 1;
            let workspace_idx = starting_workspace_idx + i as u8;

            match native_manager
                .create_spacer_by_index(window_number, workspace_idx)
                .await
            {
                Ok(spacer) => {
                    spacers.push(spacer);
                    tracing::info!(
                        "Successfully created and configured spacer window {} in workspace index {}",
                        window_number,
                        workspace_idx
                    );

                    // No need for arbitrary delays - the create_spacer_by_index method
                    // already includes confirmation that the window is properly positioned
                },
                Err(e) => {
                    tracing::error!(
                        "Failed to create spacer window {} for workspace index {}: {}",
                        window_number,
                        workspace_idx,
                        e
                    );
                    return Err(e);
                },
            }
        }

        tracing::info!("Successfully created {} spacer windows", spacers.len());
        Ok(spacers)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_constants() {
        assert_eq!(APP_NAME, "niri-spacer");
        assert!(APP_VERSION.len() > 3); // Should be a version string
        assert!(APP_DESCRIPTION.len() > 10); // Should be meaningful description

        assert_eq!(defaults::DEFAULT_WINDOW_COUNT, 9);
        assert_eq!(defaults::MIN_WINDOW_COUNT, 1);
        assert_eq!(defaults::MAX_WINDOW_COUNT, 50);
        assert_eq!(defaults::DEFAULT_SPAWN_DELAY_MS, 50);
        assert_eq!(defaults::DEFAULT_OPERATION_DELAY_MS, 25);
    }

    #[test]
    fn test_window_count_validation() {
        // Valid counts
        assert!((defaults::MIN_WINDOW_COUNT..=defaults::MAX_WINDOW_COUNT).contains(&1));
        assert!((defaults::MIN_WINDOW_COUNT..=defaults::MAX_WINDOW_COUNT).contains(&9));
        assert!((defaults::MIN_WINDOW_COUNT..=defaults::MAX_WINDOW_COUNT).contains(&50));

        // Invalid counts
        assert!(!(defaults::MIN_WINDOW_COUNT..=defaults::MAX_WINDOW_COUNT).contains(&0));
        assert!(!(defaults::MIN_WINDOW_COUNT..=defaults::MAX_WINDOW_COUNT).contains(&51));
        assert!(!(defaults::MIN_WINDOW_COUNT..=defaults::MAX_WINDOW_COUNT).contains(&100));
    }

    #[test]
    fn test_app_metadata() {
        // Verify metadata is properly set
        assert!(APP_VERSION.split('.').count() >= 2); // Should be semantic version
        assert!(APP_DESCRIPTION.len() > 10); // Should have meaningful description

        // Test that keywords are reasonable
        let expected_keywords = ["niri", "wayland", "tiling", "window-manager", "workspace"];
        // Note: Can't directly test keywords from here, but this documents expectations
        assert!(expected_keywords.contains(&"niri"));
    }

    #[test]
    #[allow(clippy::assertions_on_constants)]
    fn test_delay_constants_are_reasonable() {
        // Delays should be reasonable (not too fast, not too slow)
        assert!(defaults::DEFAULT_SPAWN_DELAY_MS >= 10);
        assert!(defaults::DEFAULT_SPAWN_DELAY_MS <= 1000);

        assert!(defaults::DEFAULT_OPERATION_DELAY_MS >= 10);
        assert!(defaults::DEFAULT_OPERATION_DELAY_MS <= 1000);

        // Operation delay should be faster than spawn delay
        assert!(defaults::DEFAULT_OPERATION_DELAY_MS <= defaults::DEFAULT_SPAWN_DELAY_MS);
    }
}
