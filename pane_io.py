#!/usr/bin/env python3
"""Shared iTerm2 pane I/O helpers for harness providers."""

from __future__ import annotations

import os
import subprocess
import tempfile
import time


PANE_PROCESSING_CACHE_TTL = 2.0
_pane_processing_cache: dict[int, tuple[float, bool | None]] = {}


def send_keystroke_to_pane(pane_index: int, chars: str) -> bool:
    """Send raw characters to an iTerm2 pane via a temp file."""
    tmp = tempfile.NamedTemporaryFile(
        mode="wb",
        suffix=".key",
        prefix="cc_key_",
        dir="/tmp",
        delete=False,
    )
    tmp.write(chars.encode("utf-8"))
    tmp.close()
    script = f'''
tell application "iTerm2"
    tell current window
        tell current tab
            tell session {pane_index}
                write text (read POSIX file "{tmp.name}")
            end tell
        end tell
    end tell
end tell
'''
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    try:
        os.unlink(tmp.name)
    except OSError:
        pass
    return result.returncode == 0


def send_enter_to_pane(pane_index: int) -> bool:
    """Submit the current prompt in an iTerm2 pane with a literal Enter keypress."""
    script = f'''
tell application "iTerm2"
    tell current window
        tell current tab
            tell session {pane_index}
                write text ""
            end tell
        end tell
    end tell
end tell
'''
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    return result.returncode == 0


def send_to_pane(pane_index: int, text: str, *, submit: bool = False) -> bool:
    """Send text to an iTerm2 pane, optionally forcing an Enter afterward."""
    tmp = tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".txt",
        prefix=f"cc_prompt_{pane_index}_",
        dir="/tmp",
        delete=False,
    )
    tmp.write(text)
    tmp.close()
    if submit or "\n" in text:
        script = f'''
tell application "iTerm2"
    tell current window
        tell current tab
            tell session {pane_index}
                write text (read POSIX file "{tmp.name}")
            end tell
        end tell
    end tell
end tell
delay 0.5
tell application "iTerm2"
    tell current window
        tell current tab
            tell session {pane_index}
                write text ""
            end tell
        end tell
    end tell
end tell
'''
    else:
        script = f'''
tell application "iTerm2"
    tell current window
        tell current tab
            tell session {pane_index}
                write text (read POSIX file "{tmp.name}")
            end tell
        end tell
    end tell
end tell
'''
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    try:
        os.unlink(tmp.name)
    except OSError:
        pass
    return result.returncode == 0


def ping_pane(pane_index: int) -> bool:
    script = f'''
tell application "iTerm2"
    tell current window
        tell current tab
            tell session {pane_index}
                write text "echo '=== PING: This is iTerm2 session/pane index {pane_index} ==="
            end tell
        end tell
    end tell
end tell
'''
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    return result.returncode == 0


def is_pane_processing(pane_index: int) -> bool | None:
    """Return iTerm2's processing state for a pane, or None if it can't be read."""
    now = time.monotonic()
    cached = _pane_processing_cache.get(pane_index)
    if cached and now - cached[0] < PANE_PROCESSING_CACHE_TTL:
        return cached[1]

    script = f'''
tell application "iTerm2"
    tell current window
        tell current tab
            tell session {pane_index}
                if is processing then
                    return "true"
                end if
                return "false"
            end tell
        end tell
    end tell
end tell
'''
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    value = None
    if result.returncode != 0:
        _pane_processing_cache[pane_index] = (now, value)
        return value
    output = result.stdout.strip().lower()
    if output == "true":
        value = True
    elif output == "false":
        value = False
    _pane_processing_cache[pane_index] = (time.monotonic(), value)
    return value
