"""Shared approval state for cross-thread synchronization.

Extracted into its own module to avoid circular imports between
client.py (CUA worker thread) and ui_server.py (async event loop).
"""

from __future__ import annotations

import threading

approval_event = threading.Event()
approval_result: dict = {"approved": False}
approval_pending: dict | None = None
