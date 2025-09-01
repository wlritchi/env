use clap::Parser;
use color_eyre::eyre::Result;
use niri_spacer::{defaults, NiriSpacer, NiriSpacerError, APP_DESCRIPTION, APP_NAME, APP_VERSION};
use tracing::{error, info, Level};
use tracing_subscriber::{EnvFilter, FmtSubscriber};

/// CLI arguments for niri-spacer
#[derive(Parser, Debug)]
#[command(name = APP_NAME)]
#[command(version = APP_VERSION)]
#[command(about = APP_DESCRIPTION)]
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

    // Initialize niri-spacer
    let mut spacer = match NiriSpacer::new().await {
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

    // Main spacer creation logic
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

            info!("niri-spacer completed successfully");
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
                NiriSpacerError::ProcessSpawn(io_err) => {
                    error!("Failed to spawn process: {}", io_err);
                    eprintln!("Error: Could not spawn terminal windows.");
                    eprintln!("Make sure 'foot' terminal and 'bash' are installed and accessible.");
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
