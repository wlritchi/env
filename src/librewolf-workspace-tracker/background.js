/**
 * Background Script - Tab Tracking and Workspace Monitoring
 *
 * This script:
 * - Tracks all open windows and their tabs
 * - Generates stable URL fingerprints for each window
 * - Periodically syncs window state with the native host
 * - Responds to tab/window events to update mappings
 */

// State management
let windowState = new Map(); // windowId -> {tabs, fingerprint, lastUpdate}
let syncTimer = null;
let isShuttingDown = false;
let restorationPending = true; // Block sync until restoration decision is made

const SYNC_INTERVAL = 30000; // 30 seconds (matches systemd timer)

/**
 * Generate a stable fingerprint from tab URLs
 * Uses SHA-256 hash of sorted URLs + tab count
 * @param {Array} tabs - Array of tab objects
 * @returns {Promise<string>} Hex-encoded fingerprint
 */
async function generateFingerprint(tabs) {
    // Extract and sort URLs (ignore internal/about pages)
    const urls = tabs
        .map(tab => tab.url)
        .filter(url => !url.startsWith("about:") && !url.startsWith("moz-extension:"))
        .sort();

    // Create fingerprint string: sorted URLs + tab count
    const fingerprintData = JSON.stringify({
        urls: urls,
        count: urls.length
    });

    // Hash with SHA-256
    const encoder = new TextEncoder();
    const data = encoder.encode(fingerprintData);
    const hashBuffer = await crypto.subtle.digest('SHA-256', data);
    const hashArray = Array.from(new Uint8Array(hashBuffer));
    const hashHex = hashArray.map(b => b.toString(16).padStart(2, '0')).join('');

    return `sha256:${hashHex}`;
}

/**
 * Get all tabs for a window
 * @param {number} windowId
 * @returns {Promise<Array>} Array of tabs
 */
async function getWindowTabs(windowId) {
    try {
        return await browser.tabs.query({ windowId: windowId });
    } catch (error) {
        console.error(`Failed to get tabs for window ${windowId}:`, error);
        return [];
    }
}

/**
 * Update state for a specific window
 * @param {number} windowId
 */
async function updateWindowState(windowId) {
    try {
        const tabs = await getWindowTabs(windowId);
        if (tabs.length === 0) {
            return;
        }

        const fingerprint = await generateFingerprint(tabs);
        const windowTitle = tabs.find(t => t.active)?.title || tabs[0]?.title || "Untitled";

        windowState.set(windowId, {
            tabs: tabs,
            fingerprint: fingerprint,
            windowTitle: windowTitle,
            lastUpdate: Date.now()
        });

        console.log(`Updated window ${windowId}: ${tabs.length} tabs, fingerprint=${fingerprint.substring(0, 16)}...`);
    } catch (error) {
        console.error(`Failed to update window ${windowId}:`, error);
    }
}

/**
 * Sync all window states with the native host
 */
async function syncAllWindows() {
    if (isShuttingDown) {
        console.log("Skipping sync (shutdown in progress)");
        return;
    }

    if (restorationPending) {
        console.log("Skipping sync (restoration pending or in progress)");
        return;
    }

    try {
        const windows = await browser.windows.getAll({ windowTypes: ["normal"] });

        // Update state for all windows
        for (const window of windows) {
            await updateWindowState(window.id);
        }

        // Send all window mappings to native host in a single batch
        // The native host will query niri once and match all windows
        if (windowState.size > 0) {
            try {
                const windowsToSync = Array.from(windowState.entries()).map(([windowId, state]) => ({
                    windowId: windowId,
                    tabs: state.tabs,
                    fingerprint: state.fingerprint,
                    windowTitle: state.windowTitle
                }));

                await storeMappingsBatch(windowsToSync);

                console.log(`Synced ${windowsToSync.length} window(s) in batch`);
            } catch (error) {
                // Native host might not be available (e.g., not running under niri)
                console.warn(`Failed to sync windows:`, error.message);
            }
        }

        // Clean up closed windows
        const activeWindowIds = new Set(windows.map(w => w.id));
        for (const windowId of windowState.keys()) {
            if (!activeWindowIds.has(windowId)) {
                windowState.delete(windowId);
                console.log(`Removed closed window ${windowId} from state`);
            }
        }
    } catch (error) {
        console.error("Failed to sync windows:", error);
    }
}

/**
 * Start the periodic sync timer
 */
function startSyncTimer() {
    if (syncTimer) {
        clearInterval(syncTimer);
    }

    // Initial sync
    syncAllWindows();

    // Periodic sync
    syncTimer = setInterval(syncAllWindows, SYNC_INTERVAL);
}

/**
 * Stop the sync timer
 */
function stopSyncTimer() {
    if (syncTimer) {
        clearInterval(syncTimer);
        syncTimer = null;
    }
}

// ========================================
// Event Listeners
// ========================================

// Tab created
browser.tabs.onCreated.addListener(async (tab) => {
    console.log("Tab created:", tab.id, "in window", tab.windowId);
    // Debounce: update after a short delay to batch rapid changes
    setTimeout(() => updateWindowState(tab.windowId), 1000);
});

// Tab removed
browser.tabs.onRemoved.addListener(async (tabId, removeInfo) => {
    console.log("Tab removed:", tabId, "from window", removeInfo.windowId);
    setTimeout(() => updateWindowState(removeInfo.windowId), 1000);
});

// Tab updated (URL changed, title changed, etc.)
browser.tabs.onUpdated.addListener(async (tabId, changeInfo, tab) => {
    if (changeInfo.url || changeInfo.title) {
        console.log("Tab updated:", tabId, changeInfo);
        setTimeout(() => updateWindowState(tab.windowId), 1000);
    }
});

// Tab activated (switched to different tab)
browser.tabs.onActivated.addListener(async (activeInfo) => {
    // Update window title in state
    setTimeout(() => updateWindowState(activeInfo.windowId), 500);
});

// Window created
browser.windows.onCreated.addListener(async (window) => {
    if (window.type === "normal") {
        console.log("Window created:", window.id);
        setTimeout(() => updateWindowState(window.id), 1000);
    }
});

// Window removed
browser.windows.onRemoved.addListener(async (windowId) => {
    console.log("Window removed:", windowId);
    windowState.delete(windowId);
});

// Extension startup
browser.runtime.onStartup.addListener(() => {
    console.log("Extension started");
    startSyncTimer();
});

// Extension installed/updated
browser.runtime.onInstalled.addListener((details) => {
    console.log("Extension installed/updated:", details.reason);
    startSyncTimer();
});

// Initialize on load
console.log("LibreWolf niri Workspace Tracker loaded");
startSyncTimer();
