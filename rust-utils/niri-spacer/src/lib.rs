//! niri-spacer: A utility to spawn placeholder windows in niri workspaces
//!
//! This library provides functionality to create and manage "spacer" windows
//! in the niri Wayland compositor to improve tiling behavior and maintain
//! consistent workspace layouts.

pub mod error;
pub mod native;
pub mod niri;
pub mod session;
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
        })
    }

    /// Initialize niri-spacer with custom native configuration
    pub async fn new_with_native_config(native_config: NativeConfig) -> Result<Self> {
        // Validate environment and detect session
        let session_info = SessionValidator::validate_environment_native_only()?;

        // Initialize managers with custom configuration
        let window_manager = WindowManager::new_with_native_config(native_config).await?;
        let workspace_manager = WorkspaceManager::new().await?;

        Ok(Self {
            session_info,
            window_manager,
            workspace_manager,
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

        // Find optimal starting workspace
        let starting_workspace_id = self
            .workspace_manager
            .suggest_starting_workspace(window_count)
            .await?;

        // Validate workspace availability
        self.workspace_manager
            .validate_workspace_availability(starting_workspace_id, window_count)
            .await?;

        // Create the spacer windows
        let spacers = self
            .window_manager
            .create_spacer_batch(window_count, starting_workspace_id)
            .await?;

        // Validate the results
        self.window_manager.validate_spacers(&spacers).await?;

        // Get final statistics
        let final_stats = self.workspace_manager.get_workspace_stats().await?;

        // Log the results
        tracing::info!(
            "Created {} spacer windows in workspaces {}-{}",
            spacers.len(),
            starting_workspace_id,
            starting_workspace_id + window_count as u64 - 1
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
