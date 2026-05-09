"""DOM-aware element annotation for precise CUA click targeting.

Queries the page DOM via Playwright for all interactive elements (links,
buttons, inputs, selects), returns their bounding boxes and text labels.
Appended to the model's input as a numbered text list so it can reference
exact coordinates instead of guessing from the screenshot.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from cua_loop.backends import BrowserBackend

_INTERACTIVE_ELEMENTS_JS = """\
const interactive = document.querySelectorAll(
  'a[href], button, input, select, textarea, [role="button"], [role="link"], ' +
  '[role="tab"], [role="menuitem"], [onclick], [tabindex]:not([tabindex="-1"])'
);
const vw = window.innerWidth;
const vh = window.innerHeight;
const results = [];
for (const el of interactive) {
  if (results.length >= 40) break;
  const rect = el.getBoundingClientRect();
  if (rect.width < 5 || rect.height < 5) continue;
  if (rect.bottom < 0 || rect.top > vh || rect.right < 0 || rect.left > vw) continue;
  const style = window.getComputedStyle(el);
  if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') continue;
  const tag = el.tagName.toLowerCase();
  let label = '';
  if (tag === 'input' || tag === 'textarea') {
    label = el.placeholder || el.getAttribute('aria-label') || el.name || el.type || '';
  } else if (tag === 'select') {
    label = el.getAttribute('aria-label') || el.name || 'dropdown';
  } else {
    label = (el.innerText || el.getAttribute('aria-label') || el.title || '').trim();
  }
  label = label.substring(0, 60).replace(/\\n/g, ' ').trim();
  if (!label) label = el.getAttribute('aria-label') || el.getAttribute('title') || tag;
  results.push({
    tag: tag,
    label: label,
    x: Math.round(rect.left + rect.width / 2),
    y: Math.round(rect.top + rect.height / 2),
    w: Math.round(rect.width),
    h: Math.round(rect.height),
    href: tag === 'a' ? (el.getAttribute('href') || '').substring(0, 100) : undefined,
    type: tag === 'input' ? el.type : undefined,
  });
}
return JSON.stringify(results);
"""


@dataclass(frozen=True)
class AnnotatedElement:
    index: int
    tag: str
    label: str
    cx: int
    cy: int
    width: int
    height: int
    href: str | None = None
    input_type: str | None = None


def get_interactive_elements(backend: BrowserBackend) -> list[AnnotatedElement]:
    if not hasattr(backend, "execute_js"):
        return []
    try:
        raw = backend.execute_js(_INTERACTIVE_ELEMENTS_JS)
    except Exception:
        return []
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(data, list):
        return []

    elements: list[AnnotatedElement] = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            continue
        elements.append(AnnotatedElement(
            index=i + 1,
            tag=item.get("tag", ""),
            label=item.get("label", ""),
            cx=item.get("x", 0),
            cy=item.get("y", 0),
            width=item.get("w", 0),
            height=item.get("h", 0),
            href=item.get("href"),
            input_type=item.get("type"),
        ))
    return elements


def format_element_map(elements: list[AnnotatedElement]) -> str:
    if not elements:
        return ""
    lines = ["[INTERACTIVE ELEMENTS ON PAGE]"]
    for el in elements:
        desc = f"[{el.index}] <{el.tag}>"
        if el.input_type:
            desc += f" type={el.input_type}"
        desc += f' "{el.label}"'
        desc += f" at ({el.cx},{el.cy}) size {el.width}x{el.height}"
        if el.href:
            desc += f" -> {el.href}"
        lines.append(desc)
    lines.append("[/INTERACTIVE ELEMENTS ON PAGE]")
    lines.append("To click an element, use its center coordinates (x,y) from the list above.")
    return "\n".join(lines)
