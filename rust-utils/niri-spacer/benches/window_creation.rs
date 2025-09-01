use criterion::{black_box, criterion_group, criterion_main, BenchmarkId, Criterion};
use niri_spacer::*;

/// Benchmark window creation and validation logic
fn benchmark_window_validation(c: &mut Criterion) {
    let mut group = c.benchmark_group("window_validation");

    for window_count in [1, 5, 10, 25, 50].iter() {
        group.bench_with_input(
            BenchmarkId::new("validate_count", window_count),
            window_count,
            |b, &count| {
                b.iter(|| {
                    let valid = (defaults::MIN_WINDOW_COUNT..=defaults::MAX_WINDOW_COUNT)
                        .contains(&black_box(count));
                    black_box(valid)
                })
            },
        );
    }

    group.finish();
}

/// Benchmark error handling performance
fn benchmark_error_handling(c: &mut Criterion) {
    let mut group = c.benchmark_group("error_handling");

    group.bench_function("create_error", |b| {
        b.iter(|| {
            let error = NiriSpacerError::InvalidWindowCount(black_box(100));
            black_box(error)
        })
    });

    group.bench_function("format_error", |b| {
        let error = NiriSpacerError::InvalidWindowCount(100);
        b.iter(|| {
            let formatted = format!("{}", black_box(&error));
            black_box(formatted)
        })
    });

    group.finish();
}

/// Benchmark workspace calculations
fn benchmark_workspace_calculations(c: &mut Criterion) {
    let mut group = c.benchmark_group("workspace_calculations");

    for count in [1, 5, 10, 25, 50].iter() {
        group.bench_with_input(
            BenchmarkId::new("workspace_range", count),
            count,
            |b, &window_count| {
                b.iter(|| {
                    let starting_workspace = black_box(1u64);
                    let ending_workspace = starting_workspace + window_count as u64 - 1;
                    black_box(ending_workspace)
                })
            },
        );
    }

    group.finish();
}

/// Benchmark string operations (socket paths, etc.)
fn benchmark_string_operations(c: &mut Criterion) {
    let mut group = c.benchmark_group("string_operations");

    group.bench_function("socket_path_validation", |b| {
        let socket_path = "/tmp/niri.sock";
        b.iter(|| {
            let valid = !black_box(socket_path).is_empty()
                && black_box(socket_path).len() < 1000
                && !black_box(socket_path).contains('\0');
            black_box(valid)
        })
    });

    group.bench_function("session_info_format", |b| {
        let session_info = NiriSessionInfo {
            socket_path: "/tmp/niri.sock".to_string(),
            wayland_display: Some("wayland-1".to_string()),
            session_type: Some("wayland".to_string()),
            session_desktop: Some("niri".to_string()),
            current_desktop: Some("niri".to_string()),
            is_wayland: true,
        };

        b.iter(|| {
            let valid = black_box(&session_info).is_valid_niri_session();
            black_box(valid)
        })
    });

    group.finish();
}

/// Benchmark workspace statistics calculations
fn benchmark_workspace_stats(c: &mut Criterion) {
    let mut group = c.benchmark_group("workspace_stats");

    // Create sample workspace statistics
    let mut workspace_counts = std::collections::HashMap::new();
    for i in 1..=10 {
        workspace_counts.insert(i, i as u32 % 3); // Varying window counts
    }

    let stats = WorkspaceStats {
        total_workspaces: 10,
        empty_workspaces: 3,
        total_windows: 15,
        spacer_windows: 5,
        focused_workspace_id: Some(1),
        workspace_window_counts: workspace_counts.clone(),
    };

    group.bench_function("calculate_summary", |b| {
        b.iter(|| {
            let summary = black_box(&stats).summary();
            black_box(summary)
        })
    });

    group.bench_function("assess_tiling_layout", |b| {
        b.iter(|| {
            let good_layout = black_box(&stats).has_good_tiling_layout();
            black_box(good_layout)
        })
    });

    group.finish();
}

/// Benchmark constant access and validation
fn benchmark_constants(c: &mut Criterion) {
    let mut group = c.benchmark_group("constants");

    group.bench_function("access_defaults", |b| {
        b.iter(|| {
            let count = black_box(defaults::DEFAULT_WINDOW_COUNT);
            let min = black_box(defaults::MIN_WINDOW_COUNT);
            let max = black_box(defaults::MAX_WINDOW_COUNT);
            let spawn_delay = black_box(defaults::DEFAULT_SPAWN_DELAY_MS);
            let op_delay = black_box(defaults::DEFAULT_OPERATION_DELAY_MS);

            black_box((count, min, max, spawn_delay, op_delay))
        })
    });

    group.bench_function("validate_app_metadata", |b| {
        b.iter(|| {
            let name = black_box(APP_NAME);
            let version = black_box(APP_VERSION);
            let description = black_box(APP_DESCRIPTION);

            black_box((name, version, description))
        })
    });

    group.finish();
}

criterion_group!(
    benches,
    benchmark_window_validation,
    benchmark_error_handling,
    benchmark_workspace_calculations,
    benchmark_string_operations,
    benchmark_workspace_stats,
    benchmark_constants
);

criterion_main!(benches);
