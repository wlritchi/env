use clap::Parser;
use color_eyre::eyre::Result;
use niri_spacer::native::NativeConfig;
use niri_spacer::signal::setup_signal_handler;
use niri_spacer::{defaults, NiriSpacer, NiriSpacerError, APP_NAME, APP_VERSION};
use std::time::Duration;
use tracing::{error, info, warn, Level};
use tracing_subscriber::{EnvFilter, FmtSubscriber};

/// CLI arguments for niri-spacer
#[derive(Parser, Debug)]
#[command(name = APP_NAME)]
#[command(version = APP_VERSION)]
#[command(
    about = "A persistent utility to spawn and manage placeholder windows in niri workspaces"
)]
#[command(long_about = None)]
struct Args {
    /// Number of spacer windows to create (1-50)
    #[arg(
        value_name = "COUNT",
        help = "Number of spacer windows to create",
        long_help = "Number of spacer windows to create. Each window will be placed in a sequential workspace (1 window per workspace) and positioned at the leftmost column for optimal tiling behavior."
    )]
    #[arg(default_value_t = defaults::DEFAULT_WINDOW_COUNT)]
    #[arg(value_parser = clap::value_parser!(u32).range(defaults::MIN_WINDOW_COUNT as i64..=defaults::MAX_WINDOW_COUNT as i64))]
    count: u32,

    /// Enable verbose logging
    #[arg(short, long, help = "Enable verbose logging output")]
    verbose: bool,

    /// Enable debug logging (implies verbose)
    #[arg(short, long, help = "Enable debug logging output (very verbose)")]
    debug: bool,

    /// Show session information and exit
    #[arg(
        long,
        help = "Show current niri session information and exit",
        long_help = "Display information about the current niri session including socket path, environment variables, and session validation status, then exit without creating any windows."
    )]
    session_info: bool,

    /// Show current workspace statistics and exit
    #[arg(
        long,
        help = "Show current workspace statistics and exit",
        long_help = "Display current workspace and window statistics including workspace utilization, window distribution, and tiling layout assessment, then exit without creating any windows."
    )]
    stats: bool,

    /// Background color for native windows (RGB hex: RRGGBB)
    #[arg(
        long,
        help = "Background color for native windows in RGB hex format",
        long_help = "Set the background color for native windows using RGB hex format (e.g., 808080 for gray, FF0000 for red). Only applies when using native windows.",
        value_name = "RRGGBB"
    )]
    native_color: Option<String>,

    /// Timeout for native window correlation (milliseconds)
    #[arg(
        long,
        help = "Timeout for correlating native windows with niri (milliseconds)",
        long_help = "Maximum time to wait for native windows to appear in niri's window list for correlation. Increase if experiencing timeout errors.",
        value_name = "MS",
        default_value = "5000"
    )]
    correlation_timeout: u64,

    /// Enable debug logging for native windows
    #[arg(
        long,
        help = "Enable detailed debug logging for native window operations",
        long_help = "Enable verbose debug output for native window creation, correlation, and lifecycle management. Useful for troubleshooting native window issues."
    )]
    debug_native: bool,
}

#[tokio::main]
async fn main() -> Result<()> {
    // Parse CLI arguments
    let args = Args::parse();

    // Initialize error handling
    color_eyre::install()?;

    // Setup logging
    setup_logging(args.debug, args.verbose)?;

    // Log startup information
    info!("{} v{} starting", APP_NAME, APP_VERSION);

    // Create configuration from CLI arguments
    let native_config = create_native_config(&args)?;

    // Log configuration
    if args.debug || args.debug_native {
        info!(
            "Native config: debug={}, timeout={}ms, color={:?}",
            native_config.debug_native,
            native_config.correlation_timeout_ms,
            native_config.background_color
        );
    }

    // Initialize niri-spacer with configuration
    let mut spacer = match NiriSpacer::new_with_native_config(native_config).await {
        Ok(spacer) => spacer,
        Err(e) => {
            match &e {
                NiriSpacerError::NotNiriSession => {
                    error!("Not running in a niri session");
                    eprintln!(
                        "Error: This tool requires running in a niri Wayland compositor session."
                    );
                    eprintln!("Make sure you're running niri and the NIRI_SOCKET environment variable is set.");
                },
                NiriSpacerError::NoSocketPath => {
                    error!("NIRI_SOCKET environment variable not set");
                    eprintln!("Error: NIRI_SOCKET environment variable not found.");
                    eprintln!("Make sure you're running this tool within a niri session.");
                },
                NiriSpacerError::SocketConnection(io_err) => {
                    error!("Failed to connect to niri: {}", io_err);
                    eprintln!("Error: Could not connect to niri IPC socket.");
                    eprintln!("Make sure niri is running and accessible.");
                },
                _ => {
                    error!("Failed to initialize: {}", e);
                    eprintln!("Error: {}", e);
                },
            }
            std::process::exit(1);
        },
    };

    // Handle info-only modes
    if args.session_info {
        return handle_session_info(&spacer).await;
    }

    if args.stats {
        return handle_stats(&mut spacer).await;
    }

    // Main spacer creation logic - application runs persistently after creating windows
    info!("Creating {} spacer windows", args.count);

    match spacer.run(args.count).await {
        Ok(spacers) => {
            println!("✓ Successfully created {} spacer windows:", spacers.len());

            for (i, spacer_window) in spacers.iter().enumerate() {
                println!(
                    "  Window {}: ID {} in workspace {}",
                    i + 1,
                    spacer_window.id,
                    spacer_window.workspace_id
                );
            }

            // Show final statistics
            if let Ok(stats) = spacer.get_stats().await {
                println!();
                println!("Final workspace state: {}", stats.summary());

                if stats.has_good_tiling_layout() {
                    println!("✓ Workspace layout optimized for tiling behavior");
                } else {
                    println!("ⓘ Consider adjusting window distribution for better tiling");
                }
            }

            info!("niri-spacer window creation completed successfully");

            // Always run in persistent mode
            info!("Entering persistent mode - press Ctrl+C to exit gracefully");
            if let Err(e) = run_persistent_mode(&mut spacer).await {
                error!("Error in persistent mode: {}", e);
                std::process::exit(1);
            }
        },
        Err(e) => {
            match &e {
                NiriSpacerError::InvalidWindowCount(count) => {
                    error!("Invalid window count: {}", count);
                    eprintln!(
                        "Error: Window count must be between {} and {}",
                        defaults::MIN_WINDOW_COUNT,
                        defaults::MAX_WINDOW_COUNT
                    );
                },
                NiriSpacerError::NativeWindowCreation(msg) => {
                    error!("Failed to create native window: {}", msg);
                    eprintln!("Error: Could not create native Wayland window.");
                    eprintln!("Make sure Wayland is available and you have proper permissions.");
                },
                NiriSpacerError::OperationTimeout => {
                    error!("Operation timed out");
                    eprintln!("Error: Timed out waiting for window operations to complete.");
                    eprintln!("This may indicate niri is overloaded or unresponsive.");
                },
                _ => {
                    error!("Operation failed: {}", e);
                    eprintln!("Error: {}", e);
                },
            }
            std::process::exit(1);
        },
    }

    Ok(())
}

/// Setup logging based on CLI flags
fn setup_logging(debug: bool, verbose: bool) -> Result<()> {
    let log_level = if debug {
        Level::DEBUG
    } else if verbose {
        Level::INFO
    } else {
        Level::WARN
    };

    let subscriber = FmtSubscriber::builder()
        .with_max_level(log_level)
        .with_env_filter(
            EnvFilter::builder()
                .with_default_directive(log_level.into())
                .from_env_lossy(),
        )
        .with_target(debug) // Show targets in debug mode
        .with_thread_ids(debug) // Show thread IDs in debug mode
        .finish();

    tracing::subscriber::set_global_default(subscriber)?;

    Ok(())
}

/// Handle session info display mode
async fn handle_session_info(spacer: &NiriSpacer) -> Result<()> {
    let session = spacer.session_info();

    println!("niri Session Information:");
    println!("========================");
    println!("Socket Path: {}", session.socket_path);
    println!(
        "Wayland Display: {}",
        session.wayland_display.as_deref().unwrap_or("not set")
    );
    println!(
        "Session Type: {}",
        session.session_type.as_deref().unwrap_or("not set")
    );
    println!(
        "Session Desktop: {}",
        session.session_desktop.as_deref().unwrap_or("not set")
    );
    println!(
        "Current Desktop: {}",
        session.current_desktop.as_deref().unwrap_or("not set")
    );
    println!("Is Wayland: {}", session.is_wayland);
    println!("Valid niri session: {}", session.is_valid_niri_session());

    let recommendations = session.get_recommendations();
    if !recommendations.is_empty() {
        println!();
        println!("Recommendations:");
        for rec in recommendations {
            println!("• {}", rec);
        }
    }

    Ok(())
}

/// Handle statistics display mode
async fn handle_stats(spacer: &mut NiriSpacer) -> Result<()> {
    match spacer.get_stats().await {
        Ok(stats) => {
            println!("Workspace Statistics:");
            println!("====================");
            println!("Total Workspaces: {}", stats.total_workspaces);
            println!("Empty Workspaces: {}", stats.empty_workspaces);
            println!("Total Windows: {}", stats.total_windows);
            println!("Spacer Windows: {}", stats.spacer_windows);
            println!(
                "Focused Workspace: {}",
                stats
                    .focused_workspace_id
                    .map_or("none".to_string(), |id| id.to_string())
            );

            println!();
            println!("Window Distribution:");
            if stats.workspace_window_counts.is_empty() {
                println!("  No windows found");
            } else {
                let mut workspaces: Vec<_> = stats.workspace_window_counts.iter().collect();
                workspaces.sort_by_key(|(id, _)| *id);

                for (workspace_id, window_count) in workspaces {
                    println!("  Workspace {}: {} windows", workspace_id, window_count);
                }
            }

            println!();
            println!(
                "Tiling Assessment: {}",
                if stats.has_good_tiling_layout() {
                    "Good tiling layout"
                } else {
                    "Could be improved"
                }
            );
        },
        Err(e) => {
            error!("Failed to get workspace statistics: {}", e);
            eprintln!("Error: Could not retrieve workspace statistics: {}", e);
            std::process::exit(1);
        },
    }

    Ok(())
}

/// Create native configuration from CLI arguments
fn create_native_config(args: &Args) -> Result<NativeConfig> {
    let mut config = NativeConfig {
        correlation_timeout_ms: args.correlation_timeout,
        debug_native: args.debug_native,
        ..Default::default()
    };

    // Parse background color if provided
    if let Some(color_str) = &args.native_color {
        config.background_color = parse_hex_color(color_str)?;
    }

    Ok(config)
}

/// Parse hex color string to RGB tuple
fn parse_hex_color(hex: &str) -> Result<(u8, u8, u8)> {
    let hex = hex.trim_start_matches('#');

    if hex.len() != 6 {
        return Err(color_eyre::eyre::eyre!(
            "Invalid hex color format '{}'. Expected 6 characters (RRGGBB)",
            hex
        ));
    }

    let r = u8::from_str_radix(&hex[0..2], 16)
        .map_err(|_| color_eyre::eyre::eyre!("Invalid red component in hex color '{}'", hex))?;
    let g = u8::from_str_radix(&hex[2..4], 16)
        .map_err(|_| color_eyre::eyre::eyre!("Invalid green component in hex color '{}'", hex))?;
    let b = u8::from_str_radix(&hex[4..6], 16)
        .map_err(|_| color_eyre::eyre::eyre!("Invalid blue component in hex color '{}'", hex))?;

    Ok((r, g, b))
}

/// Run the application in persistent mode
async fn run_persistent_mode(spacer: &mut NiriSpacer) -> Result<()> {
    // Set up signal handler for graceful shutdown
    let signal_handler = setup_signal_handler().await?;

    info!(
        "Persistent mode started with {} active spacer windows",
        spacer.get_active_spacers().len()
    );

    // Initial status report
    print_persistent_status(spacer).await;

    // Start focus monitoring to auto-redirect focus away from spacer windows
    if let Err(e) = spacer.start_focus_monitoring().await {
        warn!("Failed to start focus monitoring: {}", e);
        warn!("Focus redirection will not be available");
    }

    // Main event loop
    let mut status_interval = tokio::time::interval(Duration::from_secs(300)); // 5 minutes

    loop {
        tokio::select! {
            // Check for shutdown signal
            _ = signal_handler.wait_for_shutdown() => {
                info!("Shutdown signal received, starting graceful shutdown...");
                break;
            }

            // Print status update
            _ = status_interval.tick() => {
                print_persistent_status(spacer).await;
            }
        }
    }

    // Cleanup
    info!("Performing graceful shutdown...");
    if let Err(e) = spacer.cleanup().await {
        warn!("Error during cleanup: {}", e);
    }

    info!("niri-spacer persistent mode shutdown complete");
    Ok(())
}

/// Print status information for persistent mode
async fn print_persistent_status(spacer: &mut NiriSpacer) {
    let active_spacers = spacer.get_active_spacers();
    info!("Status: {} active spacer windows", active_spacers.len());

    if !active_spacers.is_empty() {
        info!("Active spacers:");
        for (i, spacer_window) in active_spacers.iter().enumerate().take(10) {
            info!(
                "  {}: Window {} in workspace {}",
                i + 1,
                spacer_window.id,
                spacer_window.workspace_id
            );
        }
        if active_spacers.len() > 10 {
            info!("  ... and {} more", active_spacers.len() - 10);
        }
    }

    // Get and display current workspace stats
    match spacer.get_stats().await {
        Ok(stats) => {
            info!("Workspace state: {}", stats.summary());
            if !stats.has_good_tiling_layout() {
                info!("Note: Workspace layout could be optimized");
            }
        },
        Err(e) => {
            warn!("Failed to get workspace statistics: {}", e);
        },
    }
}
