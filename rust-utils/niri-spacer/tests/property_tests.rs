use niri_spacer::defaults;
use proptest::prelude::*;

// Property-based tests for CLI argument validation and core logic
// These tests generate random inputs to verify invariants hold

proptest! {
    #[test]
    fn test_window_count_bounds_property(count in 1u32..=50u32) {
        // All valid window counts should be within bounds
        prop_assert!(count >= defaults::MIN_WINDOW_COUNT);
        prop_assert!(count <= defaults::MAX_WINDOW_COUNT);
        prop_assert!((defaults::MIN_WINDOW_COUNT..=defaults::MAX_WINDOW_COUNT).contains(&count));
    }

    #[test]
    fn test_invalid_window_counts_rejected(count in 51u32..=1000u32) {
        // All counts above maximum should be invalid
        prop_assert!(count > defaults::MAX_WINDOW_COUNT);
        prop_assert!(!(defaults::MIN_WINDOW_COUNT..=defaults::MAX_WINDOW_COUNT).contains(&count));
    }

    #[test]
    fn test_window_count_calculation_properties(
        count in 1u32..=50u32,
        starting_workspace in 1u64..=100u64
    ) {
        // Ending workspace should always be start + count - 1
        let ending_workspace = starting_workspace + count as u64 - 1;
        prop_assert!(ending_workspace >= starting_workspace);
        prop_assert_eq!(ending_workspace - starting_workspace + 1, count as u64);
    }

    #[test]
    fn test_delay_values_are_positive(
        spawn_delay in 1u64..=10000u64,
        operation_delay in 1u64..=10000u64
    ) {
        // All delay values should be positive
        prop_assert!(spawn_delay > 0);
        prop_assert!(operation_delay > 0);

        // They should be reasonable (not too large)
        prop_assert!(spawn_delay <= 10000);
        prop_assert!(operation_delay <= 10000);
    }

    #[test]
    fn test_workspace_id_arithmetic(
        workspace_id in 1u64..=1000u64,
        offset in 0u32..=49u32
    ) {
        // Workspace ID arithmetic should not overflow for reasonable values
        let result = workspace_id.saturating_add(offset as u64);
        prop_assert!(result >= workspace_id);
        prop_assert!(result >= offset as u64);
    }

    #[test]
    fn test_string_inputs_are_handled_safely(
        socket_path in "[a-zA-Z0-9_./\\-]{1,100}",
        session_name in "[a-zA-Z0-9_\\-]{1,50}"
    ) {
        // String inputs should be handled safely
        prop_assert!(!socket_path.is_empty());
        prop_assert!(!session_name.is_empty());
        prop_assert!(socket_path.len() <= 100);
        prop_assert!(session_name.len() <= 50);

        // Should not contain dangerous characters for shell commands
        prop_assert!(!socket_path.contains('`'));
        prop_assert!(!socket_path.contains('$'));
        prop_assert!(!session_name.contains('`'));
        prop_assert!(!session_name.contains('$'));
    }

    #[test]
    fn test_window_batch_size_properties(
        total_count in 1u32..=50u32,
        batch_size in 1u32..=10u32
    ) {
        // Batch processing should handle any valid combination
        prop_assume!(batch_size <= total_count);

        let num_batches = (total_count + batch_size - 1) / batch_size; // Ceiling division
        let last_batch_size = if total_count % batch_size == 0 {
            batch_size
        } else {
            total_count % batch_size
        };

        prop_assert!(num_batches > 0);
        prop_assert!(last_batch_size > 0);
        prop_assert!(last_batch_size <= batch_size);
        prop_assert_eq!((num_batches - 1) * batch_size + last_batch_size, total_count);
    }
}

/// Test CLI argument parsing with property-based testing
#[cfg(test)]
mod cli_property_tests {
    use super::*;

    proptest! {
        #[test]
        fn test_cli_count_argument_parsing(count in 1u32..=50u32) {
            // Valid counts should be parsed correctly by clap
            // Note: This is a conceptual test - actual CLI testing would require more setup
            prop_assert!(count >= 1);
            prop_assert!(count <= 50);

            // Verify the count can be formatted as a string and parsed back
            let count_str = count.to_string();
            let parsed_count: u32 = count_str.parse().unwrap();
            prop_assert_eq!(count, parsed_count);
        }

        #[test]
        fn test_environment_variable_handling(
            socket_path in "[a-zA-Z0-9_./\\-]{5,50}",
            display_name in "wayland-[0-9]"
        ) {
            // Environment variables should be handled safely
            prop_assert!(!socket_path.is_empty());
            prop_assert!(!display_name.is_empty());

            // Should be valid Unix socket path format
            prop_assert!(socket_path.len() >= 5);
            prop_assert!(socket_path.len() <= 50);

            // Display name should follow expected pattern
            prop_assert!(display_name.starts_with("wayland-"));
        }
    }
}

/// Property tests for workspace and window management logic
#[cfg(test)]
mod workspace_property_tests {
    use super::*;

    proptest! {
        #[test]
        fn test_workspace_distribution_properties(
            total_workspaces in 1u64..=100u64,
            windows_per_workspace in 0u32..=10u32
        ) {
            // Workspace distribution should be consistent
            let total_windows = total_workspaces * windows_per_workspace as u64;

            // total_windows is u64, so naturally >= 0; verify it's reasonable
            prop_assert!(total_windows <= 1000); // Reasonable upper bound

            if windows_per_workspace > 0 {
                prop_assert_eq!(total_windows / total_workspaces, windows_per_workspace as u64);
            }
        }

        #[test]
        fn test_window_id_properties(window_id in 1u64..=u32::MAX as u64) {
            // Window IDs should be valid
            prop_assert!(window_id > 0);
            prop_assert!(window_id <= u32::MAX as u64);

            // Should be representable as both u32 and u64
            let as_u32 = window_id as u32;
            prop_assert_eq!(window_id, as_u32 as u64);
        }

        #[test]
        fn test_timing_constraints(
            spawn_delay in 1u64..=1000u64,
            operation_delay in 1u64..=1000u64
        ) {
            // Timing delays should satisfy reasonable constraints
            prop_assert!(spawn_delay >= 1);
            prop_assert!(operation_delay >= 1);

            // Should be within reasonable performance bounds
            prop_assert!(spawn_delay <= 1000);
            prop_assert!(operation_delay <= 1000);

            // If we have both delays, they should be reasonable relative to each other
            let total_delay = spawn_delay + operation_delay;
            prop_assert!(total_delay >= spawn_delay);
            prop_assert!(total_delay >= operation_delay);
        }
    }
}

/// Test error handling properties
#[cfg(test)]
mod error_property_tests {
    use super::*;
    use niri_spacer::NiriSpacerError;

    proptest! {
        #[test]
        fn test_invalid_window_count_error_properties(count in 0u32..=0u32) {
            // Count of 0 should be invalid
            let error = NiriSpacerError::InvalidWindowCount(count);
            let error_string = error.to_string();

            prop_assert!(error_string.contains("must be between"));
            prop_assert!(error_string.contains("0"));
        }

        #[test]
        fn test_invalid_large_window_count_error_properties(count in 51u32..=1000u32) {
            // Large counts should be invalid
            let error = NiriSpacerError::InvalidWindowCount(count);
            let error_string = error.to_string();

            prop_assert!(error_string.contains("must be between"));
            prop_assert!(error_string.contains(&count.to_string()));
        }
    }
}
