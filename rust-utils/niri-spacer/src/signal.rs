//! Signal handling for graceful shutdown
//!
//! This module provides utilities for handling process signals like SIGINT (Ctrl+C)
//! and SIGTERM to enable graceful shutdown of the niri-spacer application.
//! The application now always runs in persistent mode.

use crate::error::{NiriSpacerError, Result};
use signal_hook::consts::{SIGINT, SIGTERM};
use signal_hook_tokio::Signals;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use tokio_stream::StreamExt;
use tracing::{debug, info, warn};

/// Signal handler for graceful shutdown
pub struct SignalHandler {
    /// Atomic boolean flag indicating whether shutdown has been requested
    pub shutdown_requested: Arc<AtomicBool>,
}

impl SignalHandler {
    /// Create a new signal handler
    pub fn new() -> Self {
        Self {
            shutdown_requested: Arc::new(AtomicBool::new(false)),
        }
    }

    /// Start the signal handling task
    ///
    /// This method spawns an async task that listens for SIGINT and SIGTERM signals
    /// and sets the shutdown flag when received.
    pub async fn start(&self) -> Result<()> {
        let shutdown_flag = self.shutdown_requested.clone();

        // Set up signal handling for SIGINT and SIGTERM
        let signals = Signals::new([SIGINT, SIGTERM]).map_err(|e| {
            NiriSpacerError::SignalHandling(format!("Failed to set up signals: {}", e))
        })?;

        info!("Signal handler initialized for SIGINT and SIGTERM");

        // Spawn a task to handle signals
        tokio::spawn(async move {
            Self::handle_signals(signals, shutdown_flag).await;
        });

        Ok(())
    }

    /// Check if shutdown has been requested
    pub fn should_shutdown(&self) -> bool {
        self.shutdown_requested.load(Ordering::Relaxed)
    }

    /// Wait for shutdown signal
    ///
    /// This method blocks until a shutdown signal is received.
    pub async fn wait_for_shutdown(&self) {
        while !self.should_shutdown() {
            tokio::time::sleep(tokio::time::Duration::from_millis(100)).await;
        }
        info!("Shutdown signal received");
    }

    /// Manually request shutdown (for testing or programmatic shutdown)
    pub fn request_shutdown(&self) {
        self.shutdown_requested.store(true, Ordering::Relaxed);
        info!("Shutdown requested programmatically");
    }

    /// Handle incoming signals
    async fn handle_signals(mut signals: Signals, shutdown_flag: Arc<AtomicBool>) {
        debug!("Signal handler task started");

        while let Some(signal) = signals.next().await {
            match signal {
                SIGINT => {
                    info!("Received SIGINT (Ctrl+C), initiating graceful shutdown");
                    shutdown_flag.store(true, Ordering::Relaxed);
                    break;
                },
                SIGTERM => {
                    info!("Received SIGTERM, initiating graceful shutdown");
                    shutdown_flag.store(true, Ordering::Relaxed);
                    break;
                },
                _ => {
                    warn!("Received unexpected signal: {}", signal);
                },
            }
        }

        debug!("Signal handler task completed");
    }
}

impl Default for SignalHandler {
    fn default() -> Self {
        Self::new()
    }
}

/// Convenience function to create and start a signal handler
pub async fn setup_signal_handler() -> Result<SignalHandler> {
    let handler = SignalHandler::new();
    handler.start().await?;
    Ok(handler)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Duration;

    #[test]
    fn test_signal_handler_creation() {
        let handler = SignalHandler::new();
        assert!(!handler.should_shutdown());
    }

    #[test]
    fn test_manual_shutdown_request() {
        let handler = SignalHandler::new();
        assert!(!handler.should_shutdown());

        handler.request_shutdown();
        assert!(handler.should_shutdown());
    }

    #[tokio::test]
    async fn test_wait_for_shutdown() {
        let _handler = SignalHandler::new();

        // Spawn a task to request shutdown after a short delay
        let handler_clone = SignalHandler::new();
        let shutdown_flag = handler_clone.shutdown_requested.clone();
        tokio::spawn(async move {
            tokio::time::sleep(Duration::from_millis(50)).await;
            shutdown_flag.store(true, Ordering::Relaxed);
        });

        // This should complete quickly due to the spawned task
        let start = std::time::Instant::now();
        handler_clone.wait_for_shutdown().await;
        let elapsed = start.elapsed();

        assert!(elapsed < Duration::from_millis(200)); // Should complete quickly
    }

    #[tokio::test]
    async fn test_setup_signal_handler() {
        // This test just verifies the setup function doesn't panic
        // Actual signal handling would require sending real signals
        let result = setup_signal_handler().await;
        assert!(result.is_ok());

        let handler = result.unwrap();
        assert!(!handler.should_shutdown());
    }
}
