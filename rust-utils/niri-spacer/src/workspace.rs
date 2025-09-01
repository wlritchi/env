use crate::error::{NiriSpacerError, Result};
use crate::niri::{NiriClient, Workspace};
use std::collections::HashMap;
use tracing::{debug, info, warn};

/// Workspace manager for niri workspace operations
pub struct WorkspaceManager {
    client: NiriClient,
}

impl WorkspaceManager {
    /// Create a new workspace manager
    pub async fn new() -> Result<Self> {
        let client = NiriClient::connect().await?;
        Ok(Self { client })
    }

    /// Get current workspaces from niri
    pub async fn get_workspaces(&mut self) -> Result<Vec<Workspace>> {
        self.client.get_workspaces().await
    }

    /// Get workspace by ID
    pub async fn get_workspace_by_id(&mut self, workspace_id: u64) -> Result<Workspace> {
        let workspaces = self.get_workspaces().await?;
        workspaces
            .into_iter()
            .find(|w| w.id == workspace_id)
            .ok_or(NiriSpacerError::WorkspaceNotFound(workspace_id))
    }

    /// Get the currently focused workspace
    pub async fn get_focused_workspace(&mut self) -> Result<Workspace> {
        let workspaces = self.get_workspaces().await?;
        workspaces
            .into_iter()
            .find(|w| w.is_focused)
            .ok_or_else(|| NiriSpacerError::IpcError("No focused workspace found".to_string()))
    }

    /// Find the next available workspace ID sequence
    pub async fn find_workspace_sequence(&mut self, count: u32) -> Result<u64> {
        let workspaces = self.get_workspaces().await?;
        let existing_ids: std::collections::HashSet<u64> =
            workspaces.into_iter().map(|w| w.id).collect();

        debug!(
            "Looking for sequence of {} workspaces, existing IDs: {:?}",
            count, existing_ids
        );

        // Start from workspace ID 1 and find a contiguous sequence
        let mut start_id = 1u64;

        loop {
            let mut all_available = true;
            for i in 0..count {
                if existing_ids.contains(&(start_id + u64::from(i))) {
                    all_available = false;
                    break;
                }
            }

            if all_available {
                debug!(
                    "Found available workspace sequence starting at {}",
                    start_id
                );
                return Ok(start_id);
            }

            start_id += 1;

            // Safety check to prevent infinite loop
            if start_id > 1000 {
                return Err(NiriSpacerError::IpcError(format!(
                    "Could not find {} consecutive available workspaces",
                    count
                )));
            }
        }
    }

    /// Check workspace utilization and suggest optimal starting workspace
    pub async fn suggest_starting_workspace(&mut self, window_count: u32) -> Result<u64> {
        let workspaces = self.get_workspaces().await?;

        // Get windows to understand workspace utilization
        let windows = self.client.get_windows().await?;
        let mut workspace_window_counts: HashMap<u64, u32> = HashMap::new();

        for window in windows {
            *workspace_window_counts
                .entry(window.workspace_id)
                .or_insert(0) += 1;
        }

        debug!(
            "Current workspace utilization: {:?}",
            workspace_window_counts
        );

        // Try to find empty workspaces first
        let empty_workspaces: Vec<_> = workspaces
            .iter()
            .filter(|w| !workspace_window_counts.contains_key(&w.id))
            .map(|w| w.id)
            .collect();

        if !empty_workspaces.is_empty() {
            // Use the lowest numbered empty workspace
            let start_id = *empty_workspaces.iter().min().unwrap();

            // Check if we have enough consecutive empty workspaces
            let mut consecutive_count = 0u32;
            for i in 0..window_count {
                let check_id = start_id + u64::from(i);
                if empty_workspaces.contains(&check_id) {
                    consecutive_count += 1;
                } else {
                    break;
                }
            }

            if consecutive_count >= window_count {
                info!("Using empty workspace sequence starting at {}", start_id);
                return Ok(start_id);
            }
        }

        // Fall back to finding any available sequence
        self.find_workspace_sequence(window_count).await
    }

    /// Validate that target workspaces are available for spacer placement
    pub async fn validate_workspace_availability(
        &mut self,
        starting_workspace_id: u64,
        count: u32,
    ) -> Result<()> {
        let workspaces = self.get_workspaces().await?;
        let existing_ids: std::collections::HashSet<u64> =
            workspaces.into_iter().map(|w| w.id).collect();

        for i in 0..count {
            let workspace_id = starting_workspace_id + u64::from(i);
            if existing_ids.contains(&workspace_id) {
                warn!(
                    "Workspace {} already exists and may contain other windows",
                    workspace_id
                );
                // Note: We don't fail here as niri allows multiple windows per workspace
                // We just warn that the workspace isn't empty
            }
        }

        debug!(
            "Workspace availability validated for sequence starting at {}",
            starting_workspace_id
        );
        Ok(())
    }

    /// Get workspace statistics for reporting
    pub async fn get_workspace_stats(&mut self) -> Result<WorkspaceStats> {
        let workspaces = self.get_workspaces().await?;
        let windows = self.client.get_windows().await?;

        let mut workspace_window_counts: HashMap<u64, u32> = HashMap::new();
        let mut spacer_windows = 0u32;

        for window in &windows {
            *workspace_window_counts
                .entry(window.workspace_id)
                .or_insert(0) += 1;

            // Count spacer windows (contains "niri-spacer window" in title)
            if window.title.contains("niri-spacer window") {
                spacer_windows += 1;
            }
        }

        let focused_workspace = workspaces.iter().find(|w| w.is_focused).map(|w| w.id);
        let total_workspaces = workspaces.len() as u32;
        let empty_workspaces = workspaces
            .iter()
            .filter(|w| !workspace_window_counts.contains_key(&w.id))
            .count() as u32;

        Ok(WorkspaceStats {
            total_workspaces,
            empty_workspaces,
            total_windows: windows.len() as u32,
            spacer_windows,
            focused_workspace_id: focused_workspace,
            workspace_window_counts,
        })
    }
}

/// Statistics about the current workspace state
#[derive(Debug)]
pub struct WorkspaceStats {
    pub total_workspaces: u32,
    pub empty_workspaces: u32,
    pub total_windows: u32,
    pub spacer_windows: u32,
    pub focused_workspace_id: Option<u64>,
    pub workspace_window_counts: HashMap<u64, u32>,
}

impl WorkspaceStats {
    /// Check if the workspace layout suggests good tiling behavior
    pub fn has_good_tiling_layout(&self) -> bool {
        // Good tiling layout:
        // - Most workspaces have 1-2 windows (focused workflow)
        // - Not too many completely empty workspaces
        // - Reasonable distribution of windows

        let workspaces_with_windows = self.workspace_window_counts.len() as u32;
        let avg_windows_per_workspace = if workspaces_with_windows > 0 {
            (self.total_windows - self.spacer_windows) as f32 / workspaces_with_windows as f32
        } else {
            0.0
        };

        // Consider layout good if average windows per workspace is between 1-3
        // and we don't have excessive empty workspaces
        (1.0..=3.0).contains(&avg_windows_per_workspace)
            && self.empty_workspaces <= self.total_workspaces / 2
    }

    /// Get a summary string for reporting
    pub fn summary(&self) -> String {
        format!(
            "{} workspaces ({} empty), {} windows ({} spacers), focused: {}",
            self.total_workspaces,
            self.empty_workspaces,
            self.total_windows,
            self.spacer_windows,
            self.focused_workspace_id
                .map_or("none".to_string(), |id| id.to_string())
        )
    }
}
