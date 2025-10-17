/**
 * Restoration Script - Automatic Workspace Restoration
 *
 * Handles automatic restoration of windows to their saved workspaces
 * when the browser starts up. Depends on background.js for generateFingerprint()
 * and native-messaging.js for restoreWorkspaces().
 */

let restorationAttempted = false;
let restorationTimer = null;

const RESTORATION_DELAY = 3000; // Wait 3 seconds after startup before restoring
const RESTORATION_TIMEOUT = 30000; // Give up after 30 seconds

/**
 * Attempt to restore all windows to their saved workspaces
 */
async function attemptRestoration() {
    if (restorationAttempted) {
        console.log("Restoration already attempted this session");
        return;
    }

    restorationAttempted = true;
    console.log("Starting workspace restoration...");

    try {
        // Request restoration from native host
        const result = await restoreWorkspaces();

        if (result.success) {
            console.log("Workspace restoration completed:", result);

            // Show notification if any windows were moved
            if (result.moved_count > 0) {
                browser.notifications.create({
                    type: "basic",
                    title: "LibreWolf Workspaces Restored",
                    message: `Moved ${result.moved_count} window(s) to saved workspaces`
                });
            }
        } else {
            console.warn("Workspace restoration failed:", result.error);
        }
    } catch (error) {
        console.error("Failed to restore workspaces:", error);

        // Don't show error notification if native host is simply not available
        // (e.g., running outside of niri)
        if (!error.message.includes("not connected")) {
            browser.notifications.create({
                type: "basic",
                title: "LibreWolf Workspace Restoration Failed",
                message: error.message
            });
        }
    } finally {
        // Re-enable sync after restoration completes (success or failure)
        // Wait an extra second to ensure windows have settled
        setTimeout(() => {
            restorationPending = false;
            console.log("Restoration complete, re-enabling sync");
        }, 1000);
    }
}

/**
 * Schedule restoration after a delay
 * This gives the browser time to fully start up and create all windows
 */
function scheduleRestoration() {
    if (restorationTimer) {
        clearTimeout(restorationTimer);
    }

    console.log(`Scheduling restoration in ${RESTORATION_DELAY}ms...`);

    restorationTimer = setTimeout(async () => {
        await attemptRestoration();
        restorationTimer = null;
    }, RESTORATION_DELAY);

    // Safety timeout to prevent restoration from hanging
    setTimeout(() => {
        if (restorationTimer) {
            clearTimeout(restorationTimer);
            restorationTimer = null;
            console.warn("Restoration timeout - giving up");
        }
    }, RESTORATION_TIMEOUT);
}

/**
 * Check if we should attempt restoration
 * Only restore if:
 * 1. Extension just started
 * 2. Multiple windows exist (session was restored)
 * 3. Not already attempted
 */
async function checkShouldRestore() {
    if (restorationAttempted) {
        return false;
    }

    const windows = await browser.windows.getAll({ windowTypes: ["normal"] });

    // If there are multiple windows, likely a session restore happened
    // If there's only one window, might be a fresh start (don't restore)
    if (windows.length > 1) {
        console.log(`Found ${windows.length} windows, will attempt restoration`);
        return true;
    }

    console.log(`Only ${windows.length} window(s), skipping restoration`);
    // Unblock sync since we're not restoring
    restorationPending = false;
    return false;
}

// ========================================
// Restoration Triggers
// ========================================

// On extension startup
browser.runtime.onStartup.addListener(async () => {
    console.log("Extension startup - checking if restoration needed");

    if (await checkShouldRestore()) {
        scheduleRestoration();
    }
});

// On extension installation (first run or update)
browser.runtime.onInstalled.addListener(async (details) => {
    if (details.reason === "install") {
        console.log("Extension installed - skipping restoration on first run");
        restorationAttempted = true; // Don't restore on first install
        restorationPending = false; // Unblock sync
    } else if (details.reason === "update") {
        console.log("Extension updated - checking if restoration needed");

        // Only restore if browser session is already running with multiple windows
        if (await checkShouldRestore()) {
            scheduleRestoration();
        }
    }
});

// Manual restoration trigger via message
browser.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (message.action === "restore_now") {
        console.log("Manual restoration triggered");
        restorationAttempted = false; // Allow re-attempting
        attemptRestoration().then(sendResponse);
        return true; // Async response
    }
});

console.log("Restoration handler loaded");
