use crate::error::{NiriSpacerError, Result};
use crate::native::{create_spacer_with_strategy, NativeConfig};
use crate::niri::{NiriClient, Window};
use tracing::{debug, info, warn};

/// Represents a spacer window that maintains workspace structure
#[derive(Debug, Clone)]
pub struct SpacerWindow {
    pub id: u64,
    pub workspace_id: u64,
    pub window_number: u32,
}

/// Window manager for creating and managing native spacer windows
pub struct WindowManager {
    client: NiriClient,
    native_config: NativeConfig,
}

impl WindowManager {
    /// Create a new window manager with default native settings
    pub async fn new() -> Result<Self> {
        let client = NiriClient::connect().await?;
        Ok(Self {
            client,
            native_config: NativeConfig::default(),
        })
    }

    /// Create a new window manager with custom native configuration
    pub async fn new_with_native_config(native_config: NativeConfig) -> Result<Self> {
        let client = NiriClient::connect().await?;
        Ok(Self {
            client,
            native_config,
        })
    }

    /// Set the native window configuration
    pub fn set_native_config(&mut self, config: NativeConfig) {
        self.native_config = config;
    }

    /// Spawn a single native spacer window
    pub async fn spawn_spacer_window(&mut self, window_number: u32) -> Result<SpacerWindow> {
        info!("Spawning native spacer window {}", window_number);
        self.spawn_native_window(window_number).await
    }

    /// Spawn a spacer window using native Wayland implementation
    async fn spawn_native_window(&self, window_number: u32) -> Result<SpacerWindow> {
        debug!("Creating native spacer window {}", window_number);

        let config = self.native_config.clone();

        // Create with temporary workspace - will be moved to correct workspace later
        create_spacer_with_strategy(&config, window_number, 1).await
    }

    /// Resize a window to minimum width to optimize tiling
    pub async fn resize_to_minimum(&mut self, window_id: u64) -> Result<()> {
        debug!("Resizing window {} to minimum width", window_id);

        // Small delay to ensure window is ready for operations
        tokio::time::sleep(tokio::time::Duration::from_millis(25)).await;

        self.client.resize_window_to_minimum(window_id).await?;

        debug!("Successfully resized window {}", window_id);
        Ok(())
    }

    /// Move a window to a specific workspace
    pub async fn move_to_workspace(&mut self, window_id: u64, workspace_id: u64) -> Result<()> {
        debug!("Moving window {} to workspace {}", window_id, workspace_id);

        self.client
            .move_window_to_workspace(window_id, workspace_id)
            .await?;

        // Small delay to ensure the move operation completes
        tokio::time::sleep(tokio::time::Duration::from_millis(25)).await;

        debug!(
            "Successfully moved window {} to workspace {}",
            window_id, workspace_id
        );
        Ok(())
    }

    /// Position a window at the leftmost column of its workspace
    pub async fn position_leftmost(&mut self, window_id: u64, workspace_id: u64) -> Result<()> {
        debug!(
            "Positioning window {} at leftmost column in workspace {}",
            window_id, workspace_id
        );

        // First, focus the target workspace
        self.client.focus_workspace(workspace_id).await?;
        tokio::time::sleep(tokio::time::Duration::from_millis(25)).await;

        // Focus the target window
        self.client.focus_window(window_id).await?;
        tokio::time::sleep(tokio::time::Duration::from_millis(25)).await;

        // Move the column to the leftmost position
        self.client.move_column_to_left().await?;
        tokio::time::sleep(tokio::time::Duration::from_millis(25)).await;

        debug!(
            "Successfully positioned window {} at leftmost column",
            window_id
        );
        Ok(())
    }

    /// Create and configure a spacer window for a specific workspace
    pub async fn create_configured_spacer(
        &mut self,
        window_number: u32,
        target_workspace_id: u64,
    ) -> Result<SpacerWindow> {
        // Spawn the window
        let mut spacer = self.spawn_spacer_window(window_number).await?;

        // Wait a moment for the window to be fully initialized
        tokio::time::sleep(tokio::time::Duration::from_millis(50)).await;

        // Move to target workspace if not already there
        if spacer.workspace_id != target_workspace_id {
            self.move_to_workspace(spacer.id, target_workspace_id)
                .await?;
            spacer.workspace_id = target_workspace_id;
        }

        // Resize to minimum width
        self.resize_to_minimum(spacer.id).await.map_err(|e| {
            warn!("Failed to resize spacer window {}: {}", spacer.id, e);
            e
        })?;

        // Position at leftmost column
        self.position_leftmost(spacer.id, target_workspace_id)
            .await
            .map_err(|e| {
                warn!(
                    "Failed to position spacer window {} leftmost: {}",
                    spacer.id, e
                );
                e
            })?;

        info!(
            "Successfully created and configured spacer window {} in workspace {}",
            window_number, target_workspace_id
        );

        Ok(spacer)
    }

    /// Get current windows from niri
    pub async fn get_windows(&mut self) -> Result<Vec<Window>> {
        self.client.get_windows().await
    }

    /// Check if a window still exists
    pub async fn window_exists(&mut self, window_id: u64) -> bool {
        match self.get_windows().await {
            Ok(windows) => windows.iter().any(|w| w.id == window_id),
            Err(e) => {
                warn!("Failed to check if window {} exists: {}", window_id, e);
                false
            },
        }
    }
}

/// Batch operations for managing multiple spacer windows
impl WindowManager {
    /// Create multiple spacer windows across sequential workspaces
    pub async fn create_spacer_batch(
        &mut self,
        window_count: u32,
        starting_workspace_id: u64,
    ) -> Result<Vec<SpacerWindow>> {
        info!(
            "Creating batch of {} spacer windows starting from workspace {}",
            window_count, starting_workspace_id
        );

        let mut spacers = Vec::with_capacity(window_count as usize);

        for i in 0..window_count {
            let window_number = i + 1;
            let workspace_id = starting_workspace_id + u64::from(i);

            match self
                .create_configured_spacer(window_number, workspace_id)
                .await
            {
                Ok(spacer) => {
                    spacers.push(spacer);

                    // Delay between spawns to avoid overwhelming niri
                    if i < window_count - 1 {
                        tokio::time::sleep(tokio::time::Duration::from_millis(50)).await;
                    }
                },
                Err(e) => {
                    warn!("Failed to create spacer window {}: {}", window_number, e);
                    return Err(e);
                },
            }
        }

        info!("Successfully created {} spacer windows", spacers.len());
        Ok(spacers)
    }

    /// Validate that all spacer windows are still present and configured
    pub async fn validate_spacers(&mut self, spacers: &[SpacerWindow]) -> Result<()> {
        debug!("Validating {} spacer windows", spacers.len());

        let current_windows = self.get_windows().await?;

        for spacer in spacers {
            if let Some(window) = current_windows.iter().find(|w| w.id == spacer.id) {
                if window.workspace_id != spacer.workspace_id {
                    warn!(
                        "Spacer window {} moved from workspace {} to {}",
                        spacer.id, spacer.workspace_id, window.workspace_id
                    );
                }
            } else {
                warn!(
                    "Spacer window {} (number {}) no longer exists",
                    spacer.id, spacer.window_number
                );
                return Err(NiriSpacerError::WindowNotFound(spacer.id));
            }
        }

        debug!("All spacer windows validated successfully");
        Ok(())
    }
}
