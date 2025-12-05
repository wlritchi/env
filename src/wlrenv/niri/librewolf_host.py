# src/wlrenv/niri/librewolf_host.py
"""Native messaging host for Librewolf workspace tracking."""

from __future__ import annotations

import json
import struct
import sys
from collections import defaultdict
from typing import Any

from wlrenv.niri import ipc, order_storage, ordering
from wlrenv.niri.librewolf import UrlMatcher
from wlrenv.niri.storage import lookup, store_entry
from wlrenv.niri.track import calculate_width_percent


def handle_message(message: dict[str, Any]) -> dict[str, Any]:
    """Handle a message from the browser extension."""
    action = message.get("action")
    request_id = message.get("request_id")

    try:
        if action == "ping":
            return {"success": True, "request_id": request_id}

        if action == "store_mappings_batch":
            return handle_store(message, request_id)

        if action == "restore_workspaces":
            return handle_restore(message, request_id)

        return {
            "success": False,
            "error": f"Unknown action: {action}",
            "request_id": request_id,
        }

    except Exception as e:
        return {"success": False, "error": str(e), "request_id": request_id}


def handle_store(message: dict[str, Any], request_id: str | None) -> dict[str, Any]:
    """Handle store_mappings_batch action."""
    windows = message.get("windows", [])

    # Sort by URL count descending for greedy matching
    windows = sorted(windows, key=lambda w: len(w.get("tabs", [])), reverse=True)

    # Get niri state once
    outputs = {o.name: o for o in ipc.get_outputs()}
    workspaces = {w.id: w for w in ipc.get_workspaces()}

    matcher = UrlMatcher.load()
    stored_count = 0

    # Track windows per workspace for ordering
    # workspace_id -> list of (column, identity)
    workspace_windows: dict[int, list[tuple[int, str]]] = defaultdict(list)

    for win in windows:
        urls = [t["url"] for t in win.get("tabs", [])]
        title = win.get("window_title", "")

        uuid = matcher.match_or_create(urls)
        niri_window = ipc.find_window_by_title(title)

        if niri_window:
            ws = workspaces.get(niri_window.workspace_id)
            if ws:
                output = outputs.get(ws.output)
                if output:
                    width = calculate_width_percent(
                        niri_window.tile_width, output.width
                    )
                    store_entry("librewolf", uuid, niri_window.workspace_id, width)
                    stored_count += 1

                    # Track column position for ordering
                    if niri_window.column is not None:
                        workspace_windows[niri_window.workspace_id].append(
                            (niri_window.column, f"librewolf:{uuid}")
                        )

    # Save column order per workspace
    for workspace_id, entries in workspace_windows.items():
        # Sort by column, extract identities
        entries.sort(key=lambda x: x[0])
        order = [identity for _, identity in entries]
        order_storage.save_order(workspace_id=workspace_id, order=order)

    matcher.save()
    return {"success": True, "stored_count": stored_count, "request_id": request_id}


def handle_restore(message: dict[str, Any], request_id: str | None) -> dict[str, Any]:
    """Handle restore_workspaces action."""
    windows = message.get("windows", [])

    # Sort by URL count descending for greedy matching
    windows = sorted(windows, key=lambda w: len(w.get("tabs", [])), reverse=True)

    matcher = UrlMatcher.load()
    moved_count = 0

    for win in windows:
        urls = [t["url"] for t in win.get("tabs", [])]
        title = win.get("window_title", "")

        uuid = matcher.match_or_create(urls)
        props = lookup("librewolf", uuid)
        niri_window = ipc.find_window_by_title(title)

        if niri_window and props:
            workspace_id = props["workspace"]
            ipc.configure(niri_window.id, workspace=workspace_id, width=props["width"])
            moved_count += 1

            # Place window in correct column order
            if niri_window.column is not None:
                ordering.place_window(
                    window_id=niri_window.id,
                    identity=f"librewolf:{uuid}",
                    workspace_id=workspace_id,
                    current_column=niri_window.column,
                )

    matcher.save()
    return {"success": True, "moved_count": moved_count, "request_id": request_id}


def read_message() -> dict[str, Any] | None:
    """Read a native messaging message from stdin."""
    raw_length = sys.stdin.buffer.read(4)
    if not raw_length:
        return None
    length = struct.unpack("@I", raw_length)[0]
    data = sys.stdin.buffer.read(length).decode("utf-8")
    return json.loads(data)  # type: ignore[no-any-return]


def write_message(message: dict[str, Any]) -> None:
    """Write a native messaging message to stdout."""
    encoded = json.dumps(message).encode("utf-8")
    sys.stdout.buffer.write(struct.pack("@I", len(encoded)))
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()


def main() -> None:
    """Main loop for native messaging host."""
    while True:
        message = read_message()
        if message is None:
            break
        response = handle_message(message)
        write_message(response)
