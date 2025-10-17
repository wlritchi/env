/**
 * Native Messaging Communication Layer
 *
 * Handles all communication with the native host (wlr-librewolf-native-host)
 * which interfaces with niri IPC to manage workspace assignments.
 */

const NATIVE_HOST_NAME = "wlr_librewolf_workspace_tracker";

/**
 * Native messaging port for persistent communication
 */
let nativePort = null;
let portReconnectTimeout = null;

/**
 * Initialize connection to native host
 */
function connectNativeHost() {
    try {
        nativePort = browser.runtime.connectNative(NATIVE_HOST_NAME);

        nativePort.onMessage.addListener((response) => {
            console.log("Native host response:", response);
        });

        nativePort.onDisconnect.addListener(() => {
            console.log("Native host disconnected:", browser.runtime.lastError);
            nativePort = null;

            // Attempt reconnection after 5 seconds
            if (portReconnectTimeout) {
                clearTimeout(portReconnectTimeout);
            }
            portReconnectTimeout = setTimeout(connectNativeHost, 5000);
        });

        console.log("Connected to native host");
    } catch (error) {
        console.error("Failed to connect to native host:", error);

        // Retry connection after 5 seconds
        if (portReconnectTimeout) {
            clearTimeout(portReconnectTimeout);
        }
        portReconnectTimeout = setTimeout(connectNativeHost, 5000);
    }
}

/**
 * Send a message to the native host
 * @param {Object} message - Message to send
 * @returns {Promise<Object>} Response from native host
 */
async function sendNativeMessage(message) {
    return new Promise((resolve, reject) => {
        if (!nativePort) {
            reject(new Error("Native host not connected"));
            return;
        }

        // Set up one-time response listener
        const listener = (response) => {
            if (response.request_id === message.request_id) {
                nativePort.onMessage.removeListener(listener);
                if (response.success) {
                    resolve(response);
                } else {
                    reject(new Error(response.error || "Unknown error"));
                }
            }
        };

        nativePort.onMessage.addListener(listener);

        // Send message
        try {
            nativePort.postMessage(message);
        } catch (error) {
            nativePort.onMessage.removeListener(listener);
            reject(error);
        }

        // Timeout after 5 seconds
        setTimeout(() => {
            nativePort.onMessage.removeListener(listener);
            reject(new Error("Native host request timeout"));
        }, 5000);
    });
}

/**
 * Store multiple window-to-workspace mappings in a single batch
 * Native host will determine workspaces by querying niri once
 * @param {Array} windows - Array of window objects with {windowId, tabs, fingerprint, windowTitle}
 * @returns {Promise<void>}
 */
async function storeMappingsBatch(windows) {
    const timestamp = new Date().toISOString();

    const message = {
        request_id: `store_mappings_batch_${Date.now()}_${Math.random()}`,
        action: "store_mappings_batch",
        windows: windows.map(w => ({
            window_id: w.windowId,
            fingerprint: w.fingerprint,
            window_title: w.windowTitle,
            tabs: w.tabs.map(tab => ({
                url: tab.url,
                title: tab.title
            })),
            tab_count: w.tabs.length
        })),
        timestamp: timestamp
    };

    await sendNativeMessage(message);
}

/**
 * Request workspace restoration for all windows
 * Sends current window fingerprints for accurate matching
 * @returns {Promise<Object>} Restoration results
 */
async function restoreWorkspaces() {
    // Collect current window state (reuse the same data we use for tracking)
    const windows = await browser.windows.getAll({ windowTypes: ["normal"] });
    const currentWindows = [];

    for (const window of windows) {
        const tabs = await browser.tabs.query({ windowId: window.id });
        if (tabs.length === 0) continue;

        const fingerprint = await generateFingerprint(tabs);
        const windowTitle = tabs.find(t => t.active)?.title || tabs[0]?.title || "Untitled";

        currentWindows.push({
            window_id: window.id,
            fingerprint: fingerprint,
            window_title: windowTitle,
            tabs: tabs.map(tab => ({
                url: tab.url,
                title: tab.title
            })),
            tab_count: tabs.length
        });
    }

    const message = {
        request_id: `restore_${Date.now()}_${Math.random()}`,
        action: "restore_workspaces",
        windows: currentWindows,
        timestamp: new Date().toISOString()
    };

    return await sendNativeMessage(message);
}

/**
 * Ping the native host to check connectivity
 * @returns {Promise<boolean>}
 */
async function pingNativeHost() {
    try {
        const message = {
            request_id: `ping_${Date.now()}`,
            action: "ping"
        };

        const response = await sendNativeMessage(message);
        return response.success === true;
    } catch (error) {
        console.error("Native host ping failed:", error);
        return false;
    }
}

// Initialize connection on load
connectNativeHost();
