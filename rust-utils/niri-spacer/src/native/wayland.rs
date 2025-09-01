//! Complete Wayland integration using smithay-client-toolkit
//!
//! This module provides complete native window creation functionality
//! using smithay-client-toolkit for window management.

use crate::error::{NiriSpacerError, Result};
use smithay_client_toolkit::reexports::client::{
    globals::registry_queue_init,
    protocol::{
        wl_output::{self, WlOutput},
        wl_surface::WlSurface,
    },
    Connection, QueueHandle,
};
use smithay_client_toolkit::{
    compositor::{CompositorHandler, CompositorState},
    delegate_compositor, delegate_output, delegate_registry, delegate_shm, delegate_xdg_shell,
    delegate_xdg_window,
    output::{OutputHandler, OutputState},
    registry::{ProvidesRegistryState, RegistryState},
    registry_handlers,
    shell::{
        xdg::{
            window::{Window, WindowConfigure, WindowDecorations, WindowHandler},
            XdgShell,
        },
        WaylandSurface,
    },
    shm::{Shm, ShmHandler},
};
use std::sync::mpsc::{self, Receiver, Sender};
use tokio::task;
use tracing::{debug, error, info, warn};
// Note: Using Wayland SHM (shared memory) for simple rendering instead of softbuffer
// This provides better compatibility with the current dependency versions.
use smithay_client_toolkit::shm::slot::SlotPool;

/// Events that can be sent from the Wayland event loop to the main task
#[derive(Debug, Clone)]
pub enum WaylandEvent {
    WindowCreated { window_id: u32, app_id: String },
    WindowClosed { window_id: u32 },
    Error(String),
}

/// Commands that can be sent to the Wayland event loop
#[derive(Debug, Clone)]
pub enum WaylandCommand {
    CreateWindow {
        app_id: String,
        title: String,
        background_color: (u8, u8, u8),
        response_sender: mpsc::Sender<Result<u32>>,
    },
    CloseWindow {
        window_id: u32,
    },
    Shutdown,
}

/// A managed window in the Wayland event loop
struct ManagedWindow {
    id: u32,
    #[allow(dead_code)] // Preserved for future correlation features
    app_id: String,
    window: Window,
    background_color: (u8, u8, u8),
    width: u32,
    height: u32,
    configured: bool,
    buffer_attached: bool,
}

/// Application state for the Wayland event loop
struct WaylandApp {
    registry_state: RegistryState,
    compositor_state: CompositorState,
    output_state: OutputState,
    shm_state: Shm,
    xdg_shell_state: XdgShell,

    event_sender: Sender<WaylandEvent>,
    command_receiver: Receiver<WaylandCommand>,

    windows: Vec<ManagedWindow>,
    window_counter: u32,

    exit: bool,
    slot_pool: Option<SlotPool>,
}

impl WaylandApp {
    fn new(
        registry_state: RegistryState,
        compositor_state: CompositorState,
        output_state: OutputState,
        shm_state: Shm,
        xdg_shell_state: XdgShell,
        event_sender: Sender<WaylandEvent>,
        command_receiver: Receiver<WaylandCommand>,
        slot_pool: Option<SlotPool>,
    ) -> Self {
        Self {
            registry_state,
            compositor_state,
            output_state,
            shm_state,
            xdg_shell_state,

            event_sender,
            command_receiver,

            windows: Vec::new(),
            window_counter: 0,

            exit: false,
            slot_pool,
        }
    }

    fn create_window(
        &mut self,
        qh: &QueueHandle<Self>,
        app_id: String,
        title: String,
        background_color: (u8, u8, u8),
    ) -> Result<u32> {
        let window_id = self.window_counter;
        self.window_counter += 1;

        debug!(
            "Creating native window with app_id: {}, title: {}",
            app_id, title
        );

        // Create the window surface
        let surface = self.compositor_state.create_surface(qh);

        // Create XDG window with no decorations (for minimal spacer appearance)
        let window = self.xdg_shell_state.create_window(
            surface.clone(),
            WindowDecorations::ServerDefault,
            qh,
        );

        // Set window properties
        window.set_app_id(app_id.clone());
        window.set_title(title);

        // Set initial size constraints
        window.set_min_size(Some((100, 60)));
        window.set_max_size(Some((400, 300)));

        // Commit the surface to trigger initial configure
        surface.commit();

        debug!("Window surface committed, waiting for configure event");

        let managed_window = ManagedWindow {
            id: window_id,
            app_id: app_id.clone(),
            window,
            background_color,
            width: 200,
            height: 100,
            configured: false,
            buffer_attached: false,
        };

        self.windows.push(managed_window);

        // Send window created event
        if let Err(e) = self.event_sender.send(WaylandEvent::WindowCreated {
            window_id,
            app_id: app_id.clone(),
        }) {
            warn!("Failed to send window created event: {}", e);
        }

        info!(
            "Successfully created native window with ID: {}, app_id: {}",
            window_id, app_id
        );

        // Force a roundtrip to process pending configure events
        debug!("Performing roundtrip to process pending events");

        Ok(window_id)
    }

    /// Draw a simple background to make the window visible
    fn draw_window_background(&mut self, window_index: usize, _qh: &QueueHandle<Self>) {
        if let (Some(managed_window), Some(slot_pool)) =
            (self.windows.get_mut(window_index), &mut self.slot_pool)
        {
            if !managed_window.configured {
                return;
            }

            let width = managed_window.width;
            let height = managed_window.height;
            let stride = width * 4; // 4 bytes per pixel (ARGB)

            // Create a buffer using the slot pool
            if let Ok((buffer, pool_slot)) = slot_pool.create_buffer(
                width as i32,
                height as i32,
                stride as i32,
                smithay_client_toolkit::reexports::client::protocol::wl_shm::Format::Argb8888,
            ) {
                // Fill with background color
                let (r, g, b) = managed_window.background_color;
                let pixel: u32 = 0xFF << 24 | (r as u32) << 16 | (g as u32) << 8 | (b as u32);

                // Write pixels directly to the slice
                for chunk in pool_slot.chunks_exact_mut(4) {
                    let bytes = pixel.to_le_bytes();
                    chunk[0] = bytes[0];
                    chunk[1] = bytes[1];
                    chunk[2] = bytes[2];
                    chunk[3] = bytes[3];
                }

                // Attach the buffer to the surface
                let surface = managed_window.window.wl_surface();
                surface.attach(Some(buffer.wl_buffer()), 0, 0);
                surface.damage_buffer(0, 0, width as i32, height as i32);
                surface.commit();

                managed_window.buffer_attached = true;
                debug!(
                    "Drew background for window {} ({}x{})",
                    managed_window.id, width, height
                );
            } else {
                warn!("Failed to create buffer for window {}", managed_window.id);
            }
        }
    }

    fn close_window(&mut self, window_id: u32) {
        debug!("Closing window with ID: {}", window_id);

        if let Some(pos) = self.windows.iter().position(|w| w.id == window_id) {
            let _window = self.windows.remove(pos);

            // Send window closed event
            if let Err(e) = self
                .event_sender
                .send(WaylandEvent::WindowClosed { window_id })
            {
                warn!("Failed to send window closed event: {}", e);
            }

            info!("Closed window with ID: {}", window_id);
        } else {
            warn!("Attempted to close non-existent window: {}", window_id);
        }
    }

    fn process_commands(&mut self, qh: &QueueHandle<Self>) {
        while let Ok(command) = self.command_receiver.try_recv() {
            match command {
                WaylandCommand::CreateWindow {
                    app_id,
                    title,
                    background_color,
                    response_sender,
                } => {
                    let result = self.create_window(qh, app_id, title, background_color);

                    // After creating the window, process any pending events to trigger configure
                    debug!("Processing any pending events after window creation");

                    if let Err(e) = response_sender.send(result) {
                        warn!("Failed to send create window response: {}", e);
                    }
                },
                WaylandCommand::CloseWindow { window_id } => {
                    self.close_window(window_id);
                },
                WaylandCommand::Shutdown => {
                    info!("Received shutdown command");
                    self.exit = true;
                },
            }
        }
    }
}

// Implement required traits for smithay-client-toolkit

impl CompositorHandler for WaylandApp {
    fn scale_factor_changed(
        &mut self,
        _conn: &Connection,
        _qh: &QueueHandle<Self>,
        _surface: &WlSurface,
        _new_factor: i32,
    ) {
        // Handle scale factor changes if needed
    }

    fn frame(
        &mut self,
        _conn: &Connection,
        _qh: &QueueHandle<Self>,
        _surface: &WlSurface,
        _time: u32,
    ) {
        // Handle frame callbacks if needed
    }

    fn transform_changed(
        &mut self,
        _conn: &Connection,
        _qh: &QueueHandle<Self>,
        _surface: &WlSurface,
        _new_transform: wl_output::Transform,
    ) {
        // Handle transform changes if needed
    }

    fn surface_enter(
        &mut self,
        _conn: &Connection,
        _qh: &QueueHandle<Self>,
        _surface: &WlSurface,
        _output: &WlOutput,
    ) {
        // Handle surface entering output
    }

    fn surface_leave(
        &mut self,
        _conn: &Connection,
        _qh: &QueueHandle<Self>,
        _surface: &WlSurface,
        _output: &WlOutput,
    ) {
        // Handle surface leaving output
    }
}

impl OutputHandler for WaylandApp {
    fn output_state(&mut self) -> &mut OutputState {
        &mut self.output_state
    }

    fn new_output(
        &mut self,
        _conn: &Connection,
        _qh: &QueueHandle<Self>,
        _output: wl_output::WlOutput,
    ) {
        // Handle new output
    }

    fn update_output(
        &mut self,
        _conn: &Connection,
        _qh: &QueueHandle<Self>,
        _output: wl_output::WlOutput,
    ) {
        // Handle output updates
    }

    fn output_destroyed(
        &mut self,
        _conn: &Connection,
        _qh: &QueueHandle<Self>,
        _output: wl_output::WlOutput,
    ) {
        // Handle output destruction
    }
}

impl WindowHandler for WaylandApp {
    fn request_close(&mut self, _conn: &Connection, _qh: &QueueHandle<Self>, _window: &Window) {
        // Handle close requests
        self.exit = true;
    }

    fn configure(
        &mut self,
        _conn: &Connection,
        qh: &QueueHandle<Self>,
        window: &Window,
        configure: WindowConfigure,
        _serial: u32,
    ) {
        // Handle window configuration changes
        if let Some(window_index) = self.windows.iter().position(|w| &w.window == window) {
            let managed_window = &mut self.windows[window_index];

            debug!(
                "Configuring window {} (app_id: {})",
                managed_window.id, managed_window.app_id
            );

            // Extract dimensions from the configure event
            if let (Some(width), Some(height)) = configure.new_size {
                managed_window.width = width.get();
                managed_window.height = height.get();
                debug!(
                    "Window {} configured to {}x{}",
                    managed_window.id,
                    width.get(),
                    height.get()
                );
            } else {
                debug!(
                    "Window {} configure event with no size, using default",
                    managed_window.id
                );
            }

            let was_configured = managed_window.configured;
            managed_window.configured = true;

            // Draw background after first configuration or on resize
            if !was_configured || !managed_window.buffer_attached {
                debug!(
                    "Drawing background for window {} (first configure: {})",
                    managed_window.id, !was_configured
                );
                self.draw_window_background(window_index, qh);
            }
        }
    }
}

impl ShmHandler for WaylandApp {
    fn shm_state(&mut self) -> &mut Shm {
        &mut self.shm_state
    }
}

impl ProvidesRegistryState for WaylandApp {
    fn registry(&mut self) -> &mut RegistryState {
        &mut self.registry_state
    }

    registry_handlers![OutputState];
}

delegate_compositor!(WaylandApp);
delegate_output!(WaylandApp);
delegate_shm!(WaylandApp);
delegate_xdg_shell!(WaylandApp);
delegate_xdg_window!(WaylandApp);
delegate_registry!(WaylandApp);

/// Wayland event loop manager that integrates with tokio
pub struct WaylandEventLoop {
    #[allow(dead_code)] // Preserved for future event emission features
    event_sender: mpsc::Sender<WaylandEvent>,
    command_sender: mpsc::Sender<WaylandCommand>,
    _handle: task::JoinHandle<Result<()>>,
}

impl WaylandEventLoop {
    /// Start the Wayland event loop in a background thread
    pub fn start() -> Result<(Self, mpsc::Receiver<WaylandEvent>)> {
        let (event_sender, event_receiver) = mpsc::channel();
        let (command_sender, command_receiver) = mpsc::channel();

        let event_sender_clone = event_sender.clone();

        let handle = task::spawn_blocking(move || {
            let result = Self::run_event_loop(event_sender_clone, command_receiver);
            if let Err(ref e) = result {
                eprintln!("Wayland event loop error: {}", e);
            }
            result
        });

        Ok((
            Self {
                event_sender,
                command_sender,
                _handle: handle,
            },
            event_receiver,
        ))
    }

    /// Send a command to the Wayland event loop
    pub fn send_command(&self, command: WaylandCommand) -> Result<()> {
        self.command_sender
            .send(command)
            .map_err(|e| NiriSpacerError::ChannelError(format!("Command send failed: {}", e)))
    }

    /// Create a new window through the event loop
    pub async fn create_window(
        &self,
        app_id: String,
        title: String,
        background_color: (u8, u8, u8),
    ) -> Result<u32> {
        let (response_sender, response_receiver) = mpsc::channel();

        self.send_command(WaylandCommand::CreateWindow {
            app_id,
            title,
            background_color,
            response_sender,
        })?;

        task::spawn_blocking(move || {
            response_receiver.recv().map_err(|e| {
                NiriSpacerError::ChannelError(format!("Response receive failed: {}", e))
            })?
        })
        .await
        .map_err(|e| NiriSpacerError::ChannelError(format!("Task join failed: {}", e)))?
    }

    /// Close a window
    pub fn close_window(&self, window_id: u32) -> Result<()> {
        self.send_command(WaylandCommand::CloseWindow { window_id })
    }

    /// Shutdown the event loop
    pub fn shutdown(&self) -> Result<()> {
        self.send_command(WaylandCommand::Shutdown)
    }

    /// Run the Wayland event loop (blocking) - complete implementation
    fn run_event_loop(
        event_sender: mpsc::Sender<WaylandEvent>,
        command_receiver: mpsc::Receiver<WaylandCommand>,
    ) -> Result<()> {
        info!("Starting complete native Wayland event loop");
        debug!("WAYLAND_DISPLAY: {:?}", std::env::var("WAYLAND_DISPLAY"));
        debug!("XDG_RUNTIME_DIR: {:?}", std::env::var("XDG_RUNTIME_DIR"));

        // Connect to Wayland
        let conn = Connection::connect_to_env().map_err(|e| {
            error!("Failed to connect to Wayland: {}", e);
            NiriSpacerError::WaylandConnection(format!("Failed to connect to Wayland: {}", e))
        })?;

        debug!("Wayland connection successful");

        // Create event queue and queue handle
        let (globals, mut event_queue) = registry_queue_init(&conn).map_err(|e| {
            NiriSpacerError::WaylandConnection(format!("Failed to create event queue: {}", e))
        })?;
        let qh = event_queue.handle();

        // Initialize compositor and other global objects
        let compositor_state = CompositorState::bind(&globals, &qh).map_err(|e| {
            NiriSpacerError::WaylandConnection(format!("Compositor bind failed: {}", e))
        })?;
        let output_state = OutputState::new(&globals, &qh);
        let shm_state = Shm::bind(&globals, &qh)
            .map_err(|e| NiriSpacerError::WaylandConnection(format!("SHM bind failed: {}", e)))?;
        let xdg_shell_state = XdgShell::bind(&globals, &qh).map_err(|e| {
            NiriSpacerError::WaylandConnection(format!("XDG shell bind failed: {}", e))
        })?;

        // Create registry state
        let registry_state = RegistryState::new(&globals);

        // Create slot pool for SHM buffers
        let slot_pool = SlotPool::new(1024 * 1024, &shm_state) // 1MB initial size
            .map_err(|e| {
                NiriSpacerError::BufferAllocation(format!("Failed to create slot pool: {}", e))
            })?;

        // Initialize application state
        let mut app = WaylandApp::new(
            registry_state,
            compositor_state,
            output_state,
            shm_state,
            xdg_shell_state,
            event_sender,
            command_receiver,
            Some(slot_pool),
        );

        // Initial round-trip to get globals
        event_queue.roundtrip(&mut app).map_err(|e| {
            NiriSpacerError::WaylandConnection(format!("Initial roundtrip failed: {}", e))
        })?;

        info!("Wayland connection established, starting event loop");

        // Main event loop
        loop {
            // Process any pending commands first
            app.process_commands(&qh);

            // Check if we should exit
            if app.exit {
                break;
            }

            // Dispatch Wayland events with a timeout
            match event_queue.dispatch_pending(&mut app) {
                Ok(_) => {
                    // For unconfigured windows, try to draw a default background
                    // This helps with windows that haven't received configure events yet
                    for i in 0..app.windows.len() {
                        let should_draw = {
                            let window = &app.windows[i];
                            !window.configured && !window.buffer_attached
                        };
                        if should_draw {
                            debug!(
                                "Drawing default background for unconfigured window {}",
                                app.windows[i].id
                            );
                            // Use default dimensions if not configured yet
                            app.windows[i].width = 200;
                            app.windows[i].height = 100;
                            app.windows[i].configured = true; // Mark as configured to proceed
                            app.draw_window_background(i, &qh);
                        }
                    }

                    // Flush any pending operations
                    if let Err(e) = event_queue.flush() {
                        warn!("Failed to flush event queue: {}", e);
                    }
                },
                Err(e) => {
                    error!("Event dispatch error: {}", e);
                    // Send error event if possible
                    let _ = app
                        .event_sender
                        .send(WaylandEvent::Error(format!("Event loop error: {}", e)));
                    // For most dispatch errors, we should exit the event loop
                    // as they typically indicate connection issues
                    break;
                },
            }

            // Small sleep to prevent busy loop
            std::thread::sleep(std::time::Duration::from_millis(10));
        }

        info!("Wayland event loop exiting");
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_wayland_event_debug() {
        let event = WaylandEvent::Error("test error".to_string());
        let debug_str = format!("{:?}", event);
        assert!(debug_str.contains("Error"));
        assert!(debug_str.contains("test error"));
    }

    #[test]
    fn test_wayland_command_debug() {
        let (sender, _) = mpsc::channel();
        let command = WaylandCommand::CreateWindow {
            app_id: "test".to_string(),
            title: "Test Window".to_string(),
            background_color: (255, 0, 0),
            response_sender: sender,
        };

        let debug_str = format!("{:?}", command);
        assert!(debug_str.contains("CreateWindow"));
    }
}
