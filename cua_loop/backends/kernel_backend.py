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
import json
import os
import time
from typing import Any

from kernel import Kernel

# Snap-to-element radius. 0 disables snapping entirely (raw model coords).
SNAP_RADIUS = int(os.getenv("CUA_SNAP_RADIUS", "30"))

# Playwright snippet: given (x, y) in the page viewport, return the centre of
# the nearest interactive ancestor element if any, plus a debug label.
_SNAP_JS = """
(async ({x, y, radius}) => {
  const SEL = 'a, button, input, select, textarea, summary, label, ' +
    '[role="button"], [role="link"], [role="checkbox"], [role="menuitem"], ' +
    '[role="tab"], [role="option"], [role="switch"], [onclick], [tabindex]';
  const isVisible = (el) => {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.display === 'none' || cs.pointerEvents === 'none') return false;
    return true;
  };
  const target = document.elementFromPoint(x, y);
  let hit = target && target.closest(SEL);
  if (hit && isVisible(hit)) {
    const r = hit.getBoundingClientRect();
    return { snapped: true, x: r.left + r.width / 2, y: r.top + r.height / 2,
             tag: hit.tagName, text: (hit.innerText || hit.value || '').trim().slice(0, 60),
             dist: 0 };
  }
  let best = null, bestDist = Infinity;
  for (const el of document.querySelectorAll(SEL)) {
    if (!isVisible(el)) continue;
    const r = el.getBoundingClientRect();
    const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
    const dx = cx - x, dy = cy - y;
    const d = Math.sqrt(dx * dx + dy * dy);
    if (d < bestDist) { bestDist = d; best = el; }
  }
  if (best && bestDist <= radius) {
    const r = best.getBoundingClientRect();
    return { snapped: true, x: r.left + r.width / 2, y: r.top + r.height / 2,
             tag: best.tagName, text: (best.innerText || best.value || '').trim().slice(0, 60),
             dist: bestDist };
  }
  return { snapped: false };
})({x: __X__, y: __Y__, radius: __R__})
"""


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

    def _exec_pw(self, code: str) -> Any:
        """Run a Playwright snippet inside the Kernel browser VM."""
        try:
            return self._kernel.browsers.execute_playwright(id=self._sid, code=code)
        except AttributeError:
            invoker = getattr(self._kernel.browsers, "playwright", None)
            if invoker is None:
                return None
            return invoker.execute(id=self._sid, code=code)

    def _snap_coords(self, x: int, y: int) -> tuple[int, int]:
        """Return (x, y) snapped to the nearest interactive DOM element.

        If snapping is disabled or anything goes wrong, returns the original
        coordinates. Failure is silent and non-fatal — the worst case is the
        same imprecise click we would have made anyway.
        """
        if SNAP_RADIUS <= 0:
            return x, y
        snippet = (
            "return " + _SNAP_JS.replace("__X__", str(x)).replace("__Y__", str(y)).replace("__R__", str(SNAP_RADIUS))
        )
        try:
            result = self._exec_pw(snippet)
            data = self._extract_value(result)
        except Exception:
            return x, y
        if not isinstance(data, dict) or not data.get("snapped"):
            return x, y
        sx, sy = int(data.get("x", x)), int(data.get("y", y))
        dist = data.get("dist", 0)
        text = (data.get("text") or "").replace("\n", " ")
        if (sx, sy) != (x, y):
            print(f"[snap] ({x},{y}) -> ({sx},{sy}) d={dist:.0f} <{data.get('tag','?')}> {text!r}")
        return sx, sy

    @staticmethod
    def _extract_value(result: Any) -> Any:
        """Pull a JSON-friendly value out of whatever execute_playwright returns."""
        if result is None:
            return None
        for attr in ("value", "result", "data", "output"):
            v = getattr(result, attr, None)
            if v is not None:
                return v
        if isinstance(result, (dict, list, str, int, float, bool)):
            return result
        # Last resort: try JSON-decoding a string-y representation
        try:
            return json.loads(str(result))
        except Exception:
            return result

    def click(self, x: int, y: int) -> None:
        sx, sy = self._snap_coords(x, y)
        self._kernel.browsers.computer.click_mouse(id=self._sid, x=sx, y=sy)

    def double_click(self, x: int, y: int) -> None:
        sx, sy = self._snap_coords(x, y)
        self._kernel.browsers.computer.click_mouse(id=self._sid, x=sx, y=sy, num_clicks=2)

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
        self._exec_pw(f"await page.goto({url!r}); return null;")

    def wait(self, seconds: float) -> None:
        time.sleep(seconds)
