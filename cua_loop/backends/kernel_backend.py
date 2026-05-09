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

# Northstar key tokens → Kernel/Chromium canonical key names.
_KEY_ALIASES = {
    "control": "Ctrl",
    "ctrl": "Ctrl",
    "command": "Meta",
    "cmd": "Meta",
    "meta": "Meta",
    "win": "Meta",
    "super": "Meta",
    "option": "Alt",
    "opt": "Alt",
    "alt": "Alt",
    "shift": "Shift",
    "enter": "Enter",
    "return": "Enter",
    "escape": "Escape",
    "esc": "Escape",
    "tab": "Tab",
    "backspace": "Backspace",
    "delete": "Delete",
    "del": "Delete",
    "space": "Space",
    "spacebar": "Space",
    "home": "Home",
    "end": "End",
    "pageup": "PageUp",
    "pagedown": "PageDown",
    "up": "ArrowUp",
    "down": "ArrowDown",
    "left": "ArrowLeft",
    "right": "ArrowRight",
    "arrowup": "ArrowUp",
    "arrowdown": "ArrowDown",
    "arrowleft": "ArrowLeft",
    "arrowright": "ArrowRight",
}


def _map_key(token: str) -> str:
    """Map a Northstar key token to Kernel's expected key name."""
    if not token:
        return ""
    lk = token.strip().lower()
    if lk in _KEY_ALIASES:
        return _KEY_ALIASES[lk]
    if len(token) == 1:
        return token  # 'a', '/', '1' — pass through, case preserved
    # Function keys F1..F24 stay capitalized; everything else gets capitalized.
    if lk.startswith("f") and lk[1:].isdigit():
        return "F" + lk[1:]
    return token.capitalize()

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
        # Stealth mode matters for marketplace sites with bot detection.
        # Viewport pinned to the same dims Northstar denormalizes against so
        # screenshot pixels and click coordinates agree.
        # Build kwargs dynamically so unknown options (across SDK versions)
        # don't blow up at type-check or runtime.
        opts: dict[str, Any] = {
            "stealth_mode": True,
            "viewport": {
                "width": int(os.getenv("CUA_DISPLAY_WIDTH", "1280")),
                "height": int(os.getenv("CUA_DISPLAY_HEIGHT", "720")),
            },
        }
        try:
            self._browser = self._kernel.browsers.create(**opts)
        except TypeError:
            # Older SDK rejects unknown kwargs — strip and retry minimally.
            try:
                self._browser = self._kernel.browsers.create(viewport=opts["viewport"])
            except TypeError:
                self._browser = self._kernel.browsers.create()
        # Canonical SDK exposes `id`; some versions use `session_id`.
        self._sid = getattr(self._browser, "id", None) or getattr(
            self._browser, "session_id", None
        )
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        if self._sid is None:
            return
        try:
            self._kernel.browsers.delete(self._sid)
        except Exception:
            pass

    def screenshot_url(self) -> str:
        result = self._kernel.browsers.computer.capture_screenshot(self._sid)
        b = _png_bytes(result)
        return "data:image/png;base64," + base64.b64encode(b).decode()

    def get_dom_state(self) -> dict[str, Any]:
        """Return a lightweight summary of the DOM state for verification."""
        snippet = """
        return {
            url: window.location.href,
            title: document.title,
            active_tag: document.activeElement ? document.activeElement.tagName : null,
            active_text: document.activeElement ? (document.activeElement.innerText || document.activeElement.value || '').slice(0, 50) : null,
            scroll_y: window.scrollY,
            html_hash: document.body ? document.body.innerHTML.length : 0 // Cheap proxy for change
        };
        """
        try:
            result = self._exec_pw("return " + snippet)
            return self._extract_value(result) or {}
        except Exception:
            return {}

    def _exec_pw(self, code: str) -> Any:
        """Run a Playwright snippet inside the Kernel browser VM.

        Canonical method per Lightcone+Kernel docs:
            kernel.browsers.playwright.execute(session_id, code=...)
        Falls back to older `execute_playwright` for SDK version skew.
        """
        pw = getattr(self._kernel.browsers, "playwright", None)
        if pw is not None:
            try:
                return pw.execute(self._sid, code=code)
            except TypeError:
                # Some versions take code positionally
                return pw.execute(self._sid, code)
        legacy = getattr(self._kernel.browsers, "execute_playwright", None)
        if legacy is not None:
            try:
                return legacy(self._sid, code=code)
            except TypeError:
                return legacy(id=self._sid, code=code)
        return None

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
        
        # If it's already a dict/list/etc, return it
        if isinstance(result, (dict, list, str, int, float, bool)):
            return result

        # Try to extract from known attributes
        for attr in ("result", "value", "data", "output"):
            v = getattr(result, attr, None)
            if v is not None:
                if isinstance(v, str) and (v.strip().startswith("{") or v.strip().startswith("[")):
                    try:
                        return json.loads(v)
                    except Exception:
                        pass
                return v

        # Try to convert to dict if it's some other object
        try:
            if hasattr(result, "model_dump"):
                return result.model_dump()
            if hasattr(result, "dict"):
                return result.dict()
        except Exception:
            pass

        # Last resort: try JSON-decoding a string-y representation
        try:
            return json.loads(str(result))
        except Exception:
            return result

    def click(self, x: int, y: int) -> None:
        sx, sy = self._snap_coords(x, y)
        self._kernel.browsers.computer.click_mouse(self._sid, sx, sy)

    def double_click(self, x: int, y: int) -> None:
        sx, sy = self._snap_coords(x, y)
        self._kernel.browsers.computer.click_mouse(self._sid, sx, sy, num_clicks=2)

    def right_click(self, x: int, y: int) -> None:
        self._kernel.browsers.computer.click_mouse(self._sid, x, y, button="right")

    def type(self, text: str) -> None:
        """Type text, converting newlines to Enter key presses."""
        if "\n" in text:
            parts = text.split("\n")
            for i, part in enumerate(parts):
                if part:
                    self._kernel.browsers.computer.type_text(self._sid, part)
                if i < len(parts) - 1:
                    self._kernel.browsers.computer.press_key(self._sid, ["Enter"])
        else:
            self._kernel.browsers.computer.type_text(self._sid, text)

    def hotkey(self, *keys: str) -> None:
        # Northstar emits things like ["Control", "c"] or ["cmd", "l"].
        # Kernel expects ["Ctrl+c"]-style combos with canonical names.
        combo = "+".join(_map_key(k) for k in keys)
        self._kernel.browsers.computer.press_key(self._sid, [combo])

    def scroll(self, dx: int, dy: int, x: int, y: int) -> None:
        self._kernel.browsers.computer.scroll(self._sid, x, y, delta_x=dx, delta_y=dy)

    def drag(self, x1: int, y1: int, x2: int, y2: int) -> None:
        self._kernel.browsers.computer.drag_mouse(
            self._sid, path=[[x1, y1], [x2, y2]]
        )

    def navigate(self, url: str) -> None:
        # Computer-controls has no direct goto; use the Playwright bridge.
        # We use a longer timeout and wait for load.
        self._exec_pw(f"await page.goto({url!r}, {{waitUntil: 'load', timeout: 30000}}); return null;")

    def wait(self, seconds: float) -> None:
        time.sleep(seconds)
