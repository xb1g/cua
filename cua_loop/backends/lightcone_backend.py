"""Lightcone-managed browser/desktop backend (the original path)."""

from __future__ import annotations

from tzafon import Lightcone


class LightconeBackend:
    def __init__(self, kind: str = "browser") -> None:
        self.kind = kind
        self._client = Lightcone()
        self._cm = None
        self._computer = None

    def __enter__(self) -> "LightconeBackend":
        self._cm = self._client.computer.create(kind=self.kind)
        self._computer = self._cm.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._cm is not None:
            self._cm.__exit__(exc_type, exc, tb)

    def screenshot_url(self) -> str:
        s = self._computer.screenshot()
        return self._computer.get_screenshot_url(s)

    def click(self, x: int, y: int) -> None:
        self._computer.click(x, y)

    def double_click(self, x: int, y: int) -> None:
        self._computer.double_click(x, y)

    def right_click(self, x: int, y: int) -> None:
        self._computer.right_click(x, y)

    def type(self, text: str) -> None:
        self._computer.type(text)

    def hotkey(self, *keys: str) -> None:
        self._computer.hotkey(*keys)

    def scroll(self, dx: int, dy: int, x: int, y: int) -> None:
        self._computer.scroll(dx, dy, x, y)

    def drag(self, x1: int, y1: int, x2: int, y2: int) -> None:
        self._computer.drag(x1, y1, x2, y2)

    def navigate(self, url: str) -> None:
        self._computer.navigate(url)

    def wait(self, seconds: float) -> None:
        self._computer.wait(seconds)
