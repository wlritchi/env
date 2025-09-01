//! Surface management for native windows
//!
//! This module handles the creation and management of Wayland surfaces
//! for native spacer windows. Simplified version without softbuffer for now.

use crate::error::Result;
use smithay_client_toolkit::output::OutputInfo;
use smithay_client_toolkit::reexports::client::protocol::wl_surface::WlSurface;
use smithay_client_toolkit::reexports::client::Connection;
use std::collections::HashMap;

/// Manager for window surfaces
pub struct SurfaceManager {
    surfaces: HashMap<u32, ManagedSurface>,
    surface_counter: u32,
}

/// A managed surface record
pub struct ManagedSurface {
    pub wl_surface: WlSurface,
    pub width: u32,
    pub height: u32,
    pub background_color: (u8, u8, u8),
}

impl SurfaceManager {
    /// Create a new surface manager
    pub fn new(_connection: &Connection) -> Result<Self> {
        Ok(Self {
            surfaces: HashMap::new(),
            surface_counter: 0,
        })
    }

    /// Register a new managed surface
    pub fn register_surface(
        &mut self,
        wl_surface: WlSurface,
        width: u32,
        height: u32,
        background_color: (u8, u8, u8),
    ) -> Result<u32> {
        let surface_id = self.surface_counter;
        self.surface_counter += 1;

        let managed_surface = ManagedSurface {
            wl_surface,
            width,
            height,
            background_color,
        };

        self.surfaces.insert(surface_id, managed_surface);
        Ok(surface_id)
    }

    /// Get the number of managed surfaces
    pub fn surface_count(&self) -> usize {
        self.surfaces.len()
    }

    /// Find a surface by its Wayland surface
    pub fn find_surface_mut(&mut self, wl_surface: &WlSurface) -> Option<&mut ManagedSurface> {
        self.surfaces
            .values_mut()
            .find(|s| &s.wl_surface == wl_surface)
    }

    /// Find a surface by its ID
    pub fn get_surface(&self, surface_id: u32) -> Option<&ManagedSurface> {
        self.surfaces.get(&surface_id)
    }

    /// Find a surface by its ID (mutable)
    pub fn get_surface_mut(&mut self, surface_id: u32) -> Option<&mut ManagedSurface> {
        self.surfaces.get_mut(&surface_id)
    }

    /// Remove a surface by its Wayland surface
    pub fn remove_surface(&mut self, wl_surface: &WlSurface) -> bool {
        let initial_len = self.surfaces.len();
        self.surfaces.retain(|_, s| &s.wl_surface != wl_surface);
        self.surfaces.len() < initial_len
    }

    /// Remove a surface by its ID
    pub fn remove_surface_by_id(&mut self, surface_id: u32) -> bool {
        self.surfaces.remove(&surface_id).is_some()
    }
}

impl ManagedSurface {
    /// Simple surface commit (no actual rendering for now)
    pub fn commit(&mut self) -> Result<()> {
        // For now, just commit the surface without rendering
        // In a full implementation, this would involve creating buffers and drawing
        self.wl_surface.commit();
        Ok(())
    }

    /// Update surface dimensions
    pub fn resize(&mut self, width: u32, height: u32) -> Result<()> {
        self.width = width;
        self.height = height;
        self.commit()
    }

    /// Change the background color
    pub fn set_background_color(&mut self, color: (u8, u8, u8)) -> Result<()> {
        self.background_color = color;
        // In a full implementation, this would trigger a re-render
        Ok(())
    }

    /// Get the Wayland surface
    pub fn wl_surface(&self) -> &WlSurface {
        &self.wl_surface
    }

    /// Get current dimensions
    pub fn dimensions(&self) -> (u32, u32) {
        (self.width, self.height)
    }
}

/// Surface configuration
#[derive(Debug, Clone)]
pub struct SurfaceConfig {
    pub initial_width: u32,
    pub initial_height: u32,
    pub background_color: (u8, u8, u8),
}

impl Default for SurfaceConfig {
    fn default() -> Self {
        Self {
            initial_width: 200,
            initial_height: 100,
            background_color: (128, 128, 128),
        }
    }
}

/// Utility function to determine optimal surface size based on output info
pub fn calculate_optimal_size(output_info: Option<&OutputInfo>) -> (u32, u32) {
    if let Some(info) = output_info {
        // Use logical_size if available, otherwise fallback to defaults
        if let Some((logical_width, logical_height)) = info.logical_size {
            // Use a small fraction of the screen size
            let width = (logical_width as f32 * 0.1) as u32;
            let height = (logical_height as f32 * 0.1) as u32;

            // Ensure minimum size
            (width.max(100), height.max(60))
        } else {
            // Default size when logical size not available
            (200, 100)
        }
    } else {
        // Default size when no output info available
        (200, 100)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_surface_config_default() {
        let config = SurfaceConfig::default();
        assert_eq!(config.initial_width, 200);
        assert_eq!(config.initial_height, 100);
        assert_eq!(config.background_color, (128, 128, 128));
    }

    #[test]
    fn test_calculate_optimal_size_no_output() {
        let (width, height) = calculate_optimal_size(None);
        assert_eq!((width, height), (200, 100));
    }

    #[test]
    fn test_surface_config_clone() {
        let config = SurfaceConfig::default();
        let cloned = config.clone();
        assert_eq!(config.initial_width, cloned.initial_width);
        assert_eq!(config.initial_height, cloned.initial_height);
        assert_eq!(config.background_color, cloned.background_color);
    }
}
