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

    /// Validate that all active spacer windows are still present
    pub async fn validate_active_spacers(&mut self) -> Result<()> {
        if self.active_spacers.is_empty() {
            return Ok(());
        }

        let result = self
            .window_manager
            .validate_spacers(&self.active_spacers)
            .await;

        if let Err(ref e) = result {
            tracing::warn!("Some spacer windows may have been lost: {}", e);
            // Update our list by removing invalid windows
            self.refresh_active_spacers().await?;
        }

        result
    }

    /// Refresh the list of active spacers by checking which ones still exist
    pub async fn refresh_active_spacers(&mut self) -> Result<()> {
        let current_windows = self.window_manager.get_windows().await?;

        // Keep only spacers that still exist
        self.active_spacers
            .retain(|spacer| current_windows.iter().any(|w| w.id == spacer.id));

        tracing::info!(
            "Refreshed active spacers list: {} windows remain",
            self.active_spacers.len()
        );
        Ok(())
    }

    /// Perform periodic maintenance tasks for persistent mode
    pub async fn perform_maintenance(&mut self) -> Result<()> {
        tracing::debug!("Performing maintenance tasks");

        // Validate active spacers
        if let Err(e) = self.validate_active_spacers().await {
            tracing::warn!("Spacer validation failed during maintenance: {}", e);
        }

        // Get current stats for logging
        if let Ok(stats) = self.get_stats().await {
            tracing::debug!("Maintenance check: {}", stats.summary());

            if !stats.has_good_tiling_layout() {
                tracing::debug!("Workspace layout could be improved");
            }
        }

        Ok(())
    }

    /// Start focus monitoring to automatically redirect focus away from spacer windows
    pub async fn start_focus_monitoring(&mut self) -> Result<()> {
        if let Some(native_manager) = self.native_manager.take() {
            tracing::info!("Starting focus event monitoring for spacer windows");

            // Start focus monitoring in a background task
            let focus_task = tokio::spawn(async move {
                if let Err(e) = native_manager.start_focus_monitoring().await {
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
