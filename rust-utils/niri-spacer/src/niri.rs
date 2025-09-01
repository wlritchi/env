use crate::error::{NiriSpacerError, Result};
use futures_util::{SinkExt, StreamExt};
use serde::{Deserialize, Serialize};
use std::path::Path;
use tokio::net::UnixStream;
use tokio_util::codec::{FramedRead, FramedWrite, LinesCodec};
use tracing::{debug, trace, warn};

/// niri IPC request message - based on actual niri IPC protocol
#[derive(Debug, Clone, Serialize)]
pub enum NiriRequest {
    Workspaces,
    Windows,
    Action(NiriAction),
    EventStream,
}

/// niri size change specification - matches niri-ipc SizeChange enum
#[derive(Debug, Clone, Serialize)]
pub enum SizeChange {
    SetFixed(i32),
    SetProportion(f64),
    AdjustFixed(i32),
    AdjustProportion(f64),
}

/// niri workspace reference - matches niri-ipc WorkspaceReferenceArg
#[derive(Debug, Clone, Serialize)]
pub enum WorkspaceReferenceArg {
    Index(u64),
    Name(String),
}

/// niri IPC actions - based on actual niri IPC protocol
#[derive(Debug, Clone, Serialize)]
pub enum NiriAction {
    Spawn {
        command: Vec<String>,
    },
    SetWindowWidth {
        id: Option<u64>,
        change: SizeChange,
    },
    SetWindowHeight {
        id: Option<u64>,
        change: SizeChange,
    },
    MoveWindowToWorkspace {
        window_id: Option<u64>,
        reference: WorkspaceReferenceArg,
        focus: bool,
    },
    FocusWindow {
        id: u64,
    },
    MoveColumnLeft {},
    FocusWorkspace {
        reference: WorkspaceReferenceArg,
    },
}

/// niri IPC response message - wrapped in Ok/Err as per niri protocol
#[derive(Debug, Clone, Deserialize)]
pub enum NiriResponse {
    Ok(ResponseData),
    Err(String),
}

/// Response data types from niri - matches the nested structure
#[derive(Debug, Clone, Deserialize)]
pub enum ResponseData {
    Workspaces(Vec<Workspace>),
    Windows(Vec<Window>),
    Event(NiriEvent),
    // For actions that return empty success - will be an empty object {}
    #[serde(other)]
    Empty,
}

/// niri workspace information - matches actual niri response format
#[derive(Debug, Clone, Deserialize)]
pub struct Workspace {
    pub id: u64, // Unique internal identifier (non-sequential)
    pub idx: u8, // Position index (1, 2, 3, etc.) - corrected type
    pub name: Option<String>,
    pub output: Option<String>, // Can be None per niri-ipc spec
    pub is_urgent: bool,
    pub is_active: bool,
    pub is_focused: bool,
    pub active_window_id: Option<u64>,
}

/// niri window information - matches actual niri response format
#[derive(Debug, Clone, Deserialize)]
pub struct Window {
    pub id: u64,
    pub title: String,
    pub app_id: String,
    pub pid: u32,
    pub workspace_id: u64,
    pub is_focused: bool,
    pub is_floating: bool,
    pub is_urgent: bool,
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

        match response {
            NiriResponse::Ok(data) => Ok(NiriResponse::Ok(data)),
            NiriResponse::Err(msg) => Err(NiriSpacerError::NiriError(msg)),
        }
    }

    /// Get list of current workspaces
    pub async fn get_workspaces(&mut self) -> Result<Vec<Workspace>> {
        let response = self.request(NiriRequest::Workspaces).await?;
        match response {
            NiriResponse::Ok(ResponseData::Workspaces(workspaces)) => Ok(workspaces),
            _ => Err(NiriSpacerError::UnexpectedResponse),
        }
    }

    /// Get list of current windows
    pub async fn get_windows(&mut self) -> Result<Vec<Window>> {
        let response = self.request(NiriRequest::Windows).await?;
        match response {
            NiriResponse::Ok(ResponseData::Windows(windows)) => Ok(windows),
            _ => Err(NiriSpacerError::UnexpectedResponse),
        }
    }

    /// Spawn a new process
    pub async fn spawn_process(&mut self, command: Vec<String>) -> Result<()> {
        let action = NiriAction::Spawn { command };
        let response = self.request(NiriRequest::Action(action)).await?;

        match response {
            NiriResponse::Ok(_) => Ok(()),
            NiriResponse::Err(msg) => Err(NiriSpacerError::NiriError(msg)),
        }
    }

    /// Resize a window to minimum width
    pub async fn resize_window_to_minimum(&mut self, window_id: u64) -> Result<()> {
        let action = NiriAction::SetWindowWidth {
            id: Some(window_id),
            change: SizeChange::SetFixed(1), // Minimum width to trigger column resize
        };

        let response = self.request(NiriRequest::Action(action)).await?;

        match response {
            NiriResponse::Ok(_) => Ok(()),
            NiriResponse::Err(msg) => Err(NiriSpacerError::WindowResize(msg)),
        }
    }

    /// Move a window to a specific workspace by index (position)
    pub async fn move_window_to_workspace_index(
        &mut self,
        window_id: u64,
        workspace_idx: u8,
    ) -> Result<()> {
        let action = NiriAction::MoveWindowToWorkspace {
            window_id: Some(window_id),
            reference: WorkspaceReferenceArg::Index(workspace_idx as u64),
            focus: false,
        };
        let response = self.request(NiriRequest::Action(action)).await?;

        match response {
            NiriResponse::Ok(_) => Ok(()),
            NiriResponse::Err(msg) => Err(NiriSpacerError::WindowMove(msg)),
        }
    }

    /// Focus a specific window
    pub async fn focus_window(&mut self, window_id: u64) -> Result<()> {
        let action = NiriAction::FocusWindow { id: window_id };
        let response = self.request(NiriRequest::Action(action)).await?;

        match response {
            NiriResponse::Ok(_) => Ok(()),
            NiriResponse::Err(msg) => Err(NiriSpacerError::WindowFocus(msg)),
        }
    }

    /// Move current column to leftmost position
    pub async fn move_column_to_left(&mut self) -> Result<()> {
        let action = NiriAction::MoveColumnLeft {};
        let response = self.request(NiriRequest::Action(action)).await?;

        match response {
            NiriResponse::Ok(_) => Ok(()),
            NiriResponse::Err(msg) => Err(NiriSpacerError::WindowMove(msg)),
        }
    }

    /// Focus a specific workspace by index (position)
    pub async fn focus_workspace_index(&mut self, workspace_idx: u8) -> Result<()> {
        let action = NiriAction::FocusWorkspace {
            reference: WorkspaceReferenceArg::Index(workspace_idx as u64),
        };
        let response = self.request(NiriRequest::Action(action)).await?;

        match response {
            NiriResponse::Ok(_) => Ok(()),
            NiriResponse::Err(msg) => Err(NiriSpacerError::NiriError(msg)),
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
