"""Browser backend protocol.

A backend abstracts away who runs the browser (Kernel, Lightcone, anything else)
so the CUA loop only depends on screenshot + action semantics.
"""

from __future__ import annotations

import os
from typing import Protocol, runtime_checkable


@runtime_checkable
class BrowserBackend(Protocol):
    def __enter__(self) -> "BrowserBackend": ...
    def __exit__(self, exc_type, exc, tb) -> None: ...

    def screenshot_url(self) -> str:
        """Return a screenshot reference Northstar's Responses API will accept.

        May be a https URL or a `data:image/png;base64,...` URL.
        """
        ...

    def get_dom_state(self) -> dict:
        """Return a lightweight summary of the DOM state for verification."""
        ...

    def click(self, x: int, y: int) -> None: ...
    def double_click(self, x: int, y: int) -> None: ...
    def right_click(self, x: int, y: int) -> None: ...
    def type(self, text: str) -> None: ...
    def hotkey(self, *keys: str) -> None: ...
    def scroll(self, dx: int, dy: int, x: int, y: int) -> None: ...
    def drag(self, x1: int, y1: int, x2: int, y2: int) -> None: ...
    def navigate(self, url: str) -> None: ...
    def wait(self, seconds: float) -> None: ...


def make_backend(kind: str = "browser") -> BrowserBackend:
    name = os.getenv("BROWSER_BACKEND", "kernel").lower()
    if name == "kernel":
        from cua_loop.backends.kernel_backend import KernelBackend

        return KernelBackend()
    if name == "lightcone":
        from cua_loop.backends.lightcone_backend import LightconeBackend

        return LightconeBackend(kind=kind)
    raise ValueError(f"unknown BROWSER_BACKEND={name!r} (expected 'kernel' or 'lightcone')")
