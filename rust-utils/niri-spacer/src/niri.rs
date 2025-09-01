use crate::error::{NiriSpacerError, Result};
use futures_util::{SinkExt, StreamExt};
use serde::{Deserialize, Serialize};
use std::path::Path;
use tokio::net::UnixStream;
use tokio_util::codec::{FramedRead, FramedWrite, LinesCodec};
use tracing::{debug, trace, warn};

/// niri IPC request message
#[derive(Debug, Clone, Serialize)]
#[serde(tag = "type")]
pub enum NiriRequest {
    #[serde(rename = "workspaces")]
    Workspaces,

    #[serde(rename = "windows")]
    Windows,

    #[serde(rename = "action")]
    Action { action: NiriAction },

    #[serde(rename = "event-stream")]
    EventStream,
}

/// niri IPC actions
#[derive(Debug, Clone, Serialize)]
#[serde(tag = "type")]
pub enum NiriAction {
    #[serde(rename = "spawn")]
    Spawn { command: Vec<String> },

    #[serde(rename = "resize-window")]
    ResizeWindow {
        window_id: u64,
        width: Option<i32>,
        height: Option<i32>,
    },

    #[serde(rename = "move-window-to-workspace")]
    MoveWindowToWorkspace { window_id: u64, workspace_id: u64 },

    #[serde(rename = "focus-window")]
    FocusWindow { window_id: u64 },

    #[serde(rename = "move-column-to-left")]
    MoveColumnToLeft,

    #[serde(rename = "focus-workspace")]
    FocusWorkspace { workspace_id: u64 },
}

/// niri IPC response message
#[derive(Debug, Clone, Deserialize)]
#[serde(tag = "type")]
pub enum NiriResponse {
    #[serde(rename = "ok")]
    Ok {
        #[serde(default)]
        data: Option<serde_json::Value>,
    },

    #[serde(rename = "error")]
    Error { msg: String },

    #[serde(rename = "workspaces")]
    Workspaces { workspaces: Vec<Workspace> },

    #[serde(rename = "windows")]
    Windows { windows: Vec<Window> },

    #[serde(rename = "event")]
    Event { event: NiriEvent },
}

/// niri workspace information
#[derive(Debug, Clone, Deserialize)]
pub struct Workspace {
    pub id: u64,
    pub name: Option<String>,
    pub is_focused: bool,
    pub is_active: bool,
}

/// niri window information
#[derive(Debug, Clone, Deserialize)]
pub struct Window {
    pub id: u64,
    pub title: String,
    pub app_id: Option<String>,
    pub workspace_id: u64,
    pub is_focused: bool,
}

/// niri event types
#[derive(Debug, Clone, Deserialize)]
#[serde(tag = "type")]
pub enum NiriEvent {
    #[serde(rename = "window-opened")]
    WindowOpened { window: Window },

    #[serde(rename = "window-closed")]
    WindowClosed { window_id: u64 },

    #[serde(rename = "window-focus-changed")]
    WindowFocusChanged { window_id: Option<u64> },

    #[serde(rename = "workspace-opened")]
    WorkspaceOpened { workspace: Workspace },

    #[serde(rename = "workspace-closed")]
    WorkspaceClosed { workspace_id: u64 },

    #[serde(rename = "workspace-focus-changed")]
    WorkspaceFocusChanged { workspace_id: u64 },
}

/// niri IPC client for communication with niri compositor
pub struct NiriClient {
    stream: UnixStream,
}

impl NiriClient {
    /// Connect to niri IPC socket
    pub async fn connect() -> Result<Self> {
        let socket_path =
            std::env::var("NIRI_SOCKET").map_err(|_| NiriSpacerError::NoSocketPath)?;

        if !Path::new(&socket_path).exists() {
            return Err(NiriSpacerError::InvalidSocketPath(socket_path));
        }

        debug!("Connecting to niri socket at: {}", socket_path);
        let stream = UnixStream::connect(&socket_path).await?;

        Ok(Self { stream })
    }

    /// Send a request and wait for response
    pub async fn request(&mut self, request: NiriRequest) -> Result<NiriResponse> {
        let (read_half, write_half) = self.stream.split();
        let mut writer = FramedWrite::new(write_half, LinesCodec::new());
        let mut reader = FramedRead::new(read_half, LinesCodec::new());

        // Serialize and send request
        let request_json = serde_json::to_string(&request)?;
        trace!("Sending request: {}", request_json);
        writer
            .send(request_json)
            .await
            .map_err(|e| NiriSpacerError::IpcError(e.to_string()))?;

        // Read and deserialize response
        let response_line = reader
            .next()
            .await
            .ok_or_else(|| NiriSpacerError::IpcError("No response received".to_string()))?
            .map_err(|e| NiriSpacerError::IpcError(e.to_string()))?;

        trace!("Received response: {}", response_line);
        let response: NiriResponse = serde_json::from_str(&response_line)?;

        // Check for error responses
        if let NiriResponse::Error { msg } = &response {
            return Err(NiriSpacerError::NiriError(msg.clone()));
        }

        Ok(response)
    }

    /// Get list of current workspaces
    pub async fn get_workspaces(&mut self) -> Result<Vec<Workspace>> {
        let response = self.request(NiriRequest::Workspaces).await?;
        match response {
            NiriResponse::Workspaces { workspaces } => Ok(workspaces),
            _ => Err(NiriSpacerError::UnexpectedResponse),
        }
    }

    /// Get list of current windows
    pub async fn get_windows(&mut self) -> Result<Vec<Window>> {
        let response = self.request(NiriRequest::Windows).await?;
        match response {
            NiriResponse::Windows { windows } => Ok(windows),
            _ => Err(NiriSpacerError::UnexpectedResponse),
        }
    }

    /// Spawn a new process
    pub async fn spawn_process(&mut self, command: Vec<String>) -> Result<()> {
        let action = NiriAction::Spawn { command };
        let response = self.request(NiriRequest::Action { action }).await?;

        match response {
            NiriResponse::Ok { .. } => Ok(()),
            NiriResponse::Error { msg } => Err(NiriSpacerError::NiriError(msg)),
            _ => Err(NiriSpacerError::UnexpectedResponse),
        }
    }

    /// Resize a window to minimum width
    pub async fn resize_window_to_minimum(&mut self, window_id: u64) -> Result<()> {
        let action = NiriAction::ResizeWindow {
            window_id,
            width: Some(1), // Minimum width to trigger column resize
            height: None,
        };

        let response = self.request(NiriRequest::Action { action }).await?;

        match response {
            NiriResponse::Ok { .. } => Ok(()),
            NiriResponse::Error { msg } => Err(NiriSpacerError::WindowResize(msg)),
            _ => Err(NiriSpacerError::UnexpectedResponse),
        }
    }

    /// Move a window to a specific workspace
    pub async fn move_window_to_workspace(
        &mut self,
        window_id: u64,
        workspace_id: u64,
    ) -> Result<()> {
        let action = NiriAction::MoveWindowToWorkspace {
            window_id,
            workspace_id,
        };
        let response = self.request(NiriRequest::Action { action }).await?;

        match response {
            NiriResponse::Ok { .. } => Ok(()),
            NiriResponse::Error { msg } => Err(NiriSpacerError::WindowMove(msg)),
            _ => Err(NiriSpacerError::UnexpectedResponse),
        }
    }

    /// Focus a specific window
    pub async fn focus_window(&mut self, window_id: u64) -> Result<()> {
        let action = NiriAction::FocusWindow { window_id };
        let response = self.request(NiriRequest::Action { action }).await?;

        match response {
            NiriResponse::Ok { .. } => Ok(()),
            NiriResponse::Error { msg } => Err(NiriSpacerError::WindowFocus(msg)),
            _ => Err(NiriSpacerError::UnexpectedResponse),
        }
    }

    /// Move current column to leftmost position
    pub async fn move_column_to_left(&mut self) -> Result<()> {
        let action = NiriAction::MoveColumnToLeft;
        let response = self.request(NiriRequest::Action { action }).await?;

        match response {
            NiriResponse::Ok { .. } => Ok(()),
            NiriResponse::Error { msg } => Err(NiriSpacerError::WindowMove(msg)),
            _ => Err(NiriSpacerError::UnexpectedResponse),
        }
    }

    /// Focus a specific workspace
    pub async fn focus_workspace(&mut self, workspace_id: u64) -> Result<()> {
        let action = NiriAction::FocusWorkspace { workspace_id };
        let response = self.request(NiriRequest::Action { action }).await?;

        match response {
            NiriResponse::Ok { .. } => Ok(()),
            NiriResponse::Error { msg } => Err(NiriSpacerError::NiriError(msg)),
            _ => Err(NiriSpacerError::UnexpectedResponse),
        }
    }
}

/// Wait for a specific window to appear with timeout
pub async fn wait_for_window_spawn(window_title_contains: &str) -> Result<Window> {
    let timeout = tokio::time::Duration::from_secs(5);
    let mut interval = tokio::time::interval(tokio::time::Duration::from_millis(100));

    let start = tokio::time::Instant::now();

    loop {
        if start.elapsed() > timeout {
            return Err(NiriSpacerError::OperationTimeout);
        }

        interval.tick().await;

        let mut client = NiriClient::connect().await?;
        match client.get_windows().await {
            Ok(windows) => {
                if let Some(window) = windows
                    .into_iter()
                    .find(|w| w.title.contains(window_title_contains))
                {
                    debug!("Found spawned window: {} (id: {})", window.title, window.id);
                    return Ok(window);
                }
            },
            Err(e) => {
                warn!("Failed to get windows while waiting: {}", e);
            },
        }
    }
}
