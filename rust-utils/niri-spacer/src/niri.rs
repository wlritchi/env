use crate::error::{NiriSpacerError, Result};
use futures_util::{SinkExt, Stream, StreamExt};
use serde::{Deserialize, Serialize};
use std::path::Path;
use tokio::io::AsyncWriteExt;
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
    FocusColumnRight {},
    FocusColumnLeft {},
    CenterColumn {},
    SetColumnWidth {
        change: SizeChange,
    },
    MaximizeColumn {},
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

/// niri event types - matches actual niri event format like {"WindowFocusChanged":{"id":123}}
#[derive(Debug, Clone, Deserialize)]
pub enum NiriEvent {
    WindowOpened {
        window: Window,
    },
    WindowClosed {
        window_id: u64,
    },
    WindowFocusChanged {
        id: u64,
    },
    WorkspaceActiveWindowChanged {
        workspace_id: u64,
        active_window_id: u64,
    },
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

    /// Focus the column to the right
    pub async fn focus_column_right(&mut self) -> Result<()> {
        let action = NiriAction::FocusColumnRight {};
        let response = self.request(NiriRequest::Action(action)).await?;

        match response {
            NiriResponse::Ok(_) => Ok(()),
            NiriResponse::Err(msg) => Err(NiriSpacerError::WindowFocus(msg)),
        }
    }

    /// Focus the column to the left
    pub async fn focus_column_left(&mut self) -> Result<()> {
        let action = NiriAction::FocusColumnLeft {};
        let response = self.request(NiriRequest::Action(action)).await?;

        match response {
            NiriResponse::Ok(_) => Ok(()),
            NiriResponse::Err(msg) => Err(NiriSpacerError::WindowFocus(msg)),
        }
    }

    /// Center the current column to fix layout positioning
    pub async fn center_column(&mut self) -> Result<()> {
        let action = NiriAction::CenterColumn {};
        let response = self.request(NiriRequest::Action(action)).await?;

        match response {
            NiriResponse::Ok(_) => Ok(()),
            NiriResponse::Err(msg) => Err(NiriSpacerError::WindowMove(msg)),
        }
    }

    /// Set the width of the current column
    pub async fn set_column_width(&mut self, change: SizeChange) -> Result<()> {
        let action = NiriAction::SetColumnWidth { change };
        let response = self.request(NiriRequest::Action(action)).await?;

        match response {
            NiriResponse::Ok(_) => Ok(()),
            NiriResponse::Err(msg) => Err(NiriSpacerError::WindowResize(msg)),
        }
    }

    /// Maximize the current column
    pub async fn maximize_column(&mut self) -> Result<()> {
        let action = NiriAction::MaximizeColumn {};
        let response = self.request(NiriRequest::Action(action)).await?;

        match response {
            NiriResponse::Ok(_) => Ok(()),
            NiriResponse::Err(msg) => Err(NiriSpacerError::WindowResize(msg)),
        }
    }

    /// Subscribe to niri events and return an async stream
    pub async fn subscribe_to_events(mut self) -> Result<impl Stream<Item = Result<NiriEvent>>> {
        use futures_util::stream::StreamExt;
        use tokio_util::codec::FramedRead;

        // Send event stream request
        let request = NiriRequest::EventStream;
        let request_json = serde_json::to_string(&request)?;

        self.stream.write_all(request_json.as_bytes()).await?;
        self.stream.write_all(b"\n").await?;

        // Create a stream from the socket
        let reader = FramedRead::new(self.stream, LinesCodec::new());

        Ok(reader.filter_map(move |line_result| async move {
            match line_result {
                Ok(line) => {
                    // Skip the initial response confirmation (e.g., {"Ok":null})
                    // Events can be in format like {"WindowFocusChanged":{"id":123}} or {"Ok":null}
                    match serde_json::from_str::<serde_json::Value>(&line) {
                        Ok(value) => {
                            // Skip confirmation responses like {"Ok":null}
                            if value.get("Ok").is_some() || value.get("Err").is_some() {
                                debug!("Skipping IPC response confirmation: {}", line);
                                None
                            } else {
                                // Try to parse as event - events have keys like "WindowFocusChanged", etc.
                                debug!("Processing potential event: {}", line);
                                match serde_json::from_str::<NiriEvent>(&line) {
                                    Ok(event) => Some(Ok(event)),
                                    Err(e) => {
                                        debug!(
                                            "Failed to parse as event, skipping: {} - Error: {}",
                                            line, e
                                        );
                                        None // Skip lines we can't parse as events
                                    },
                                }
                            }
                        },
                        Err(_) => {
                            // If we can't parse as JSON, skip it
                            None
                        },
                    }
                },
                Err(e) => Some(Err(NiriSpacerError::IpcError(e.to_string()))),
            }
        }))
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
