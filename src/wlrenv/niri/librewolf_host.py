# src/wlrenv/niri/librewolf_host.py
"""Native messaging host for Librewolf workspace tracking."""

from __future__ import annotations

import json
import logging
import os
import struct
import sys
from collections import defaultdict
from typing import Any, cast

from wlrenv.niri import config, ipc, ordering, positions
from wlrenv.niri.librewolf import UrlMatcher
from wlrenv.niri.positions import PositionEntry
from wlrenv.niri.track import calculate_width_percent

logger = logging.getLogger(__name__)


def _get_window_column(window_id: int, workspace_id: int) -> int | None:
    """Get the current column of a window."""
    windows = ipc.get_windows()
    for w in windows:
        if w.id == window_id and w.workspace_id == workspace_id:
            return w.column
    return None


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
    logger.info("handle_store: processing %d windows", len(windows))

    # Sort by URL count descending for greedy matching
    windows = sorted(windows, key=lambda w: len(w.get("tabs", [])), reverse=True)

    # Get niri state once
    outputs = {o.name: o for o in ipc.get_outputs()}
    workspaces = {w.id: w for w in ipc.get_workspaces()}
    logger.info(
        "handle_store: niri state has %d outputs, %d workspaces",
        len(outputs),
        len(workspaces),
    )

    # Log all librewolf window titles niri sees for debugging
    all_niri_windows = ipc.get_windows()
    librewolf_titles = [
        (w.id, w.title[:60] if w.title else None)
        for w in all_niri_windows
        if w.app_id and "librewolf" in w.app_id.lower()
    ]
    logger.info("handle_store: niri librewolf windows: %s", librewolf_titles)

    # Log browser-reported titles for comparison
    browser_titles = [
        (w.get("window_title", "")[:60] if w.get("window_title") else None)
        for w in windows
    ]
    logger.info("handle_store: browser-reported titles: %s", browser_titles)

    matcher = UrlMatcher.load()
    stored_count = 0
    matched_window_ids: set[int] = set()

    # Collect entries per workspace
    workspace_entries: dict[int, list[PositionEntry]] = defaultdict(list)

    for win in windows:
        urls = [t["url"] for t in win.get("tabs", [])]
        title = win.get("window_title", "")

        uuid = matcher.match_or_create(urls)
        stable_id = f"librewolf:{uuid}"
        niri_window = ipc.find_window_by_title(title, exclude_ids=matched_window_ids)

        if niri_window:
            matched_window_ids.add(niri_window.id)
            ws = workspaces.get(niri_window.workspace_id)
            if ws:
                output = outputs.get(ws.output)
                if output and niri_window.column is not None:
                    width = calculate_width_percent(
                        niri_window.tile_width, output.width
                    )
                    workspace_entries[niri_window.workspace_id].append(
                        PositionEntry(
                            id=stable_id,
                            index=niri_window.column,
                            window_id=niri_window.id,
                            width=width,
                        )
                    )
                    stored_count += 1
                    logger.info(
                        "handle_store: [%s] matched window_id=%d ws=%d col=%d width=%d",
                        stable_id,
                        niri_window.id,
                        niri_window.workspace_id,
                        niri_window.column,
                        width,
                    )
                else:
                    logger.warning(
                        "handle_store: [%s] window found but output=%s column=%s",
                        stable_id,
                        output.name if output else None,
                        niri_window.column,
                    )
            else:
                logger.warning(
                    "handle_store: [%s] window found but workspace_id=%d not in workspaces",
                    stable_id,
                    niri_window.workspace_id,
                )
        else:
            logger.warning(
                "handle_store: no niri window found for title=%r (uuid=%s)",
                title[:50] if title else None,
                uuid,
            )

    # Upsert entries per workspace
    for workspace_id, entries in workspace_entries.items():
        positions.upsert_entries(
            app="librewolf", workspace_id=workspace_id, entries=entries
        )

    # Prune dominated boots
    if workspace_entries:
        positions.prune_dominated_boots(positions.get_boot_id())

    matcher.save()
    logger.info("handle_store: completed, stored_count=%d", stored_count)
    return {"success": True, "stored_count": stored_count, "request_id": request_id}


def handle_restore(message: dict[str, Any], request_id: str | None) -> dict[str, Any]:
    """Handle restore_workspaces action."""
    windows = message.get("windows", [])
    logger.info("handle_restore: processing %d windows", len(windows))

    # Sort by URL count descending for greedy matching
    windows = sorted(windows, key=lambda w: len(w.get("tabs", [])), reverse=True)

    matcher = UrlMatcher.load()
    moved_count = 0
    matched_window_ids: set[int] = set()
    restored_entries: dict[int, list[PositionEntry]] = defaultdict(list)

    for win in windows:
        urls = [t["url"] for t in win.get("tabs", [])]
        title = win.get("window_title", "")

        uuid = matcher.match_or_create(urls)
        stable_id = f"librewolf:{uuid}"
        props = positions.lookup_latest_position(stable_id)
        niri_window = ipc.find_window_by_title(title, exclude_ids=matched_window_ids)

        if niri_window and props:
            matched_window_ids.add(niri_window.id)
            workspace_id = props.workspace_id
            logger.info(
                "handle_restore: [%s] restoring to workspace %d width %d",
                stable_id,
                workspace_id,
                props.width,
            )
            ipc.configure(niri_window.id, workspace=workspace_id, width=props.width)
            moved_count += 1

            # Place window in correct column order
            column = _get_window_column(niri_window.id, workspace_id)
            logger.info(
                "handle_restore: [%s] after configure, column=%s",
                stable_id,
                column,
            )
            if column is not None:
                ordering.place_window(
                    window_id=niri_window.id,
                    identity=stable_id,
                    workspace_id=workspace_id,
                    current_column=column,
                )
                # Record entry for position storage
                restored_entries[workspace_id].append(
                    PositionEntry(
                        id=stable_id,
                        index=column,
                        window_id=niri_window.id,
                        width=props.width,
                    )
                )
            else:
                logger.warning(
                    "handle_restore: [%s] column is None after configure, skipping placement!",
                    stable_id,
                )

    # Record restored positions
    for workspace_id, entries in restored_entries.items():
        positions.upsert_entries("librewolf", workspace_id, entries)

    if restored_entries:
        positions.prune_dominated_boots(positions.get_boot_id())

    matcher.save()
    logger.info("handle_restore: completed, moved_count=%d", moved_count)
    return {"success": True, "moved_count": moved_count, "request_id": request_id}


def read_message() -> dict[str, Any] | None:
    """Read a native messaging message from stdin."""
    raw_length = sys.stdin.buffer.read(4)
    if not raw_length:
        return None
    length = struct.unpack("@I", raw_length)[0]
    data = sys.stdin.buffer.read(length).decode("utf-8")
    return cast(dict[str, Any], json.loads(data))


def write_message(message: dict[str, Any]) -> None:
    """Write a native messaging message to stdout."""
    encoded = json.dumps(message).encode("utf-8")
    sys.stdout.buffer.write(struct.pack("@I", len(encoded)))
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()


def _setup_logging() -> None:
    """Configure logging for native messaging host.

    Logs to a file since stdout is used for the native messaging protocol.
    Enabled via NIRI_DEBUG environment variable.
    """
    level_str = os.environ.get("NIRI_DEBUG", "").upper()
    if level_str in ("1", "TRUE", "INFO"):
        level = logging.INFO
    elif level_str == "DEBUG":
        level = logging.DEBUG
    else:
        return  # No logging if not enabled

    log_file = config.STATE_DIR / "librewolf-host.log"
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=level,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
        filename=str(log_file),
        filemode="a",
    )
    logger.info("=== Native messaging host started ===")


def main() -> None:
    """Main loop for native messaging host."""
    _setup_logging()
    while True:
        message = read_message()
        if message is None:
            break
        response = handle_message(message)
        write_message(response)
