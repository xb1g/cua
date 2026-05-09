"""Kernel browser backend.

Uses Kernel's cloud browsers for the surface and Northstar for the brain.
Reads `KERNEL_API_KEY` from env (handled by the SDK).

Notes:
- Northstar's screenshot input accepts data URLs, so we base64 the PNG bytes
  Kernel returns rather than uploading anywhere.
- `navigate` is implemented via the Playwright execution endpoint because the
  computer-controls surface does not expose a `goto` directly.
"""

from __future__ import annotations

import base64
import time
from typing import Any

from kernel import Kernel


def _png_bytes(result: Any) -> bytes:
    """Coerce whatever capture_screenshot returns into PNG bytes.

    Stainless SDKs vary: bytes, BinaryAPIResponse-like, or an object with
    .content / .image / .data / .read() / .iter_bytes().
    """
    if isinstance(result, (bytes, bytearray)):
        return bytes(result)
    for attr in ("content", "image", "data"):
        v = getattr(result, attr, None)
        if isinstance(v, (bytes, bytearray)):
            return bytes(v)
    if hasattr(result, "iter_bytes"):
        return b"".join(result.iter_bytes())
    if hasattr(result, "read"):
        return result.read()
    raise RuntimeError(f"could not extract PNG bytes from kernel screenshot: {type(result)!r}")


class KernelBackend:
    def __init__(self) -> None:
        self._kernel = Kernel()
        self._browser: Any = None
        self._sid: str | None = None

    def __enter__(self) -> "KernelBackend":
        self._browser = self._kernel.browsers.create()
        self._sid = self._browser.session_id
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._sid is None:
            return
        try:
            self._kernel.browsers.delete(id=self._sid)
        except Exception:
            pass

    def screenshot_url(self) -> str:
        result = self._kernel.browsers.computer.capture_screenshot(id=self._sid)
        b = _png_bytes(result)
        return "data:image/png;base64," + base64.b64encode(b).decode()

    def click(self, x: int, y: int) -> None:
        self._kernel.browsers.computer.click_mouse(id=self._sid, x=x, y=y)

    def double_click(self, x: int, y: int) -> None:
        self._kernel.browsers.computer.click_mouse(id=self._sid, x=x, y=y, num_clicks=2)

    def right_click(self, x: int, y: int) -> None:
        self._kernel.browsers.computer.click_mouse(id=self._sid, x=x, y=y, button="right")

    def type(self, text: str) -> None:
        self._kernel.browsers.computer.type_text(id=self._sid, text=text)

    def hotkey(self, *keys: str) -> None:
        # Northstar emits a list of key tokens (e.g. ["ctrl","t"]).
        # Kernel expects ["Ctrl+t"]-style combos.
        combo = "+".join(k.capitalize() if len(k) > 1 else k for k in keys)
        self._kernel.browsers.computer.press_key(id=self._sid, keys=[combo])

    def scroll(self, dx: int, dy: int, x: int, y: int) -> None:
        # Kernel exposes delta_y; horizontal scroll is uncommon and we drop dx for now.
        self._kernel.browsers.computer.scroll(id=self._sid, x=x, y=y, delta_y=dy)

    def drag(self, x1: int, y1: int, x2: int, y2: int) -> None:
        self._kernel.browsers.computer.drag_mouse(
            id=self._sid, path=[[x1, y1], [x2, y2]]
        )

    def navigate(self, url: str) -> None:
        # Computer-controls has no direct goto; use the Playwright bridge.
        code = f"await page.goto({url!r}); return null;"
        try:
            self._kernel.browsers.execute_playwright(id=self._sid, code=code)
        except AttributeError:
            # SDK shape differs across versions; fall back to a generic invoker.
            invoker = getattr(self._kernel.browsers, "playwright", None)
            if invoker is None:
                raise
            invoker.execute(id=self._sid, code=code)

    def wait(self, seconds: float) -> None:
        time.sleep(seconds)
