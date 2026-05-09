"""Model-agnostic provider abstraction for CUA models.

Supports multiple backend providers:
- northstar: Tzafon Lightcone API (default, original behavior)
- fireworks: Fireworks AI OpenAI-compatible API (Kimi K2.6 Turbo)

Configuration via environment variables:
  CUA_MODEL_PROVIDER=northstar|fireworks  (default: northstar)
  
Northstar:
  TZAFON_API_KEY or LIGHTCONE_API_KEY
  NORTHSTAR_MODEL (default: tzafon.northstar-cua-fast)
  
Fireworks / Kimi K2.6 Turbo:
  FIREWORKS_API_KEY
  FIREWORKS_BASE_URL (default: https://api.fireworks.ai/inference/v1)
  FIREWORKS_MODEL (default: accounts/fireworks/routers/kimi-k2p6-turbo)
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal


# ---------------------------------------------------------------------------
# Common response wrapper — normalises both Lightcone and OpenAI shapes
# ---------------------------------------------------------------------------


@dataclass
class ComputerCallAction:
    """Normalised action emitted by a CUA model."""

    type: str
    x: int | None = None
    y: int | None = None
    end_x: int | None = None
    end_y: int | None = None
    text: str | None = None
    keys: list[str] | None = None
    url: str | None = None
    scroll_x: int | None = None
    scroll_y: int | None = None
    button: str | None = None
    status: str | None = None
    result: str | None = None

    @classmethod
    def from_lightcone(cls, action: Any) -> "ComputerCallAction":
        return cls(
            type=getattr(action, "type", "unknown"),
            x=getattr(action, "x", None),
            y=getattr(action, "y", None),
            end_x=getattr(action, "end_x", None),
            end_y=getattr(action, "end_y", None),
            text=getattr(action, "text", None),
            keys=list(getattr(action, "keys", []) or []),
            url=getattr(action, "url", None),
            scroll_x=getattr(action, "scroll_x", None),
            scroll_y=getattr(action, "scroll_y", None),
            button=getattr(action, "button", None),
            status=getattr(action, "status", None),
            result=getattr(action, "result", None),
        )

    @classmethod
    def from_openai_tool(cls, tool_call: Any) -> "ComputerCallAction":
        """Parse an OpenAI tool_call into a normalised action.

        Fireworks/Kimi computer-use tool calls contain JSON arguments with
        action type and coordinates.
        """
        import json

        name = getattr(tool_call, "function", None) and getattr(tool_call.function, "name", "")
        arguments = getattr(tool_call, "function", None) and getattr(tool_call.function, "arguments", "{}") or "{}"
        if isinstance(arguments, str):
            try:
                args = json.loads(arguments)
            except json.JSONDecodeError:
                args = {}
        else:
            args = arguments

        action_type = name or args.get("action", "unknown")
        # Strip common prefixes that providers add
        if action_type.startswith("computer_"):
            action_type = action_type.replace("computer_", "")

        return cls(
            type=action_type,
            x=args.get("x"),
            y=args.get("y"),
            end_x=args.get("end_x"),
            end_y=args.get("end_y"),
            text=args.get("text"),
            keys=args.get("keys", []),
            url=args.get("url"),
            scroll_x=args.get("scroll_x"),
            scroll_y=args.get("scroll_y"),
            button=args.get("button"),
            status=args.get("status"),
            result=args.get("result"),
        )


@dataclass
class ResponseItem:
    """One item in a model response (computer_call or message)."""

    type: Literal["computer_call", "message"]
    call_id: str | None = None
    action: ComputerCallAction | None = None
    content: list[dict[str, Any]] = field(default_factory=list)

    @property
    def text(self) -> str | None:
        """Convenience accessor for message text."""
        for block in self.content:
            if isinstance(block, dict) and block.get("type") == "text":
                return block.get("text", "")
        return None


@dataclass
class ModelResponse:
    """Normalised response from any CUA provider."""

    id: str
    output: list[ResponseItem] = field(default_factory=list)
    raw: Any = None  # provider-specific raw response, for debugging


# ---------------------------------------------------------------------------
# Provider interface
# ---------------------------------------------------------------------------


class ModelProvider(ABC):
    """Abstract base for CUA model providers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable provider name."""

    @property
    @abstractmethod
    def model_name(self) -> str:
        """The model identifier used in API calls."""

    @abstractmethod
    def create_response(
        self,
        input_messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        previous_response_id: str | None = None,
    ) -> ModelResponse:
        """Send a turn to the model and return a normalised response."""

    @abstractmethod
    def get_tools(self, display_width: int, display_height: int) -> list[dict[str, Any]]:
        """Return tool definitions in the provider-native format."""

    def _make_system_message(self, text: str) -> dict[str, Any]:
        return {"role": "system", "content": text}


# ---------------------------------------------------------------------------
# Northstar / Lightcone provider (original behaviour)
# ---------------------------------------------------------------------------


class NorthstarProvider(ModelProvider):
    """Tzafon Lightcone API — the original Northstar CUA backend."""

    def __init__(self) -> None:
        from tzafon import Lightcone

        self._client = Lightcone(timeout=120.0)
        self._model = os.getenv("NORTHSTAR_MODEL", "tzafon.northstar-cua-fast")

    @property
    def name(self) -> str:
        return "northstar"

    @property
    def model_name(self) -> str:
        return self._model

    def get_tools(self, display_width: int, display_height: int) -> list[dict[str, Any]]:
        return [
            {
                "type": "computer_use",
                "display_width": display_width,
                "display_height": display_height,
                "environment": "desktop",
            }
        ]

    def create_response(
        self,
        input_messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        previous_response_id: str | None = None,
    ) -> ModelResponse:
        kwargs: dict[str, Any] = {
            "model": self._model,
            "input": input_messages,
        }
        if tools:
            kwargs["tools"] = tools
        if previous_response_id:
            kwargs["previous_response_id"] = previous_response_id

        raw = self._client.responses.create(**kwargs)

        output: list[ResponseItem] = []
        for item in raw.output or []:
            if item.type == "computer_call":
                output.append(
                    ResponseItem(
                        type="computer_call",
                        call_id=getattr(item, "call_id", None),
                        action=ComputerCallAction.from_lightcone(item.action),
                    )
                )
            elif item.type == "message":
                content = []
                for block in item.content or []:
                    if getattr(block, "text", None):
                        content.append({"type": "text", "text": block.text})
                output.append(ResponseItem(type="message", content=content))

        return ModelResponse(id=raw.id, output=output, raw=raw)


# ---------------------------------------------------------------------------
# Fireworks AI provider (OpenAI-compatible — Kimi K2.6 Turbo)
# ---------------------------------------------------------------------------


class FireworksProvider(ModelProvider):
    """Fireworks AI OpenAI-compatible API for Kimi K2.6 Turbo and other models.

    Uses the standard OpenAI SDK with a custom base_url.
    Computer-use actions are exposed as an OpenAI function tool.
    """

    def __init__(self) -> None:
        from openai import OpenAI

        api_key = os.getenv("FIREWORKS_API_KEY", "")
        if not api_key:
            raise RuntimeError(
                "FIREWORKS_API_KEY is required when CUA_MODEL_PROVIDER=fireworks. "
                "Get one at https://fireworks.ai/account"
            )

        self._base_url = os.getenv("FIREWORKS_BASE_URL", "https://api.fireworks.ai/inference/v1")
        self._model = os.getenv("FIREWORKS_MODEL", "accounts/fireworks/routers/kimi-k2p6-turbo")
        self._client = OpenAI(api_key=api_key, base_url=self._base_url, timeout=120.0)

    @property
    def name(self) -> str:
        return "fireworks"

    @property
    def model_name(self) -> str:
        return self._model

    def get_tools(self, display_width: int, display_height: int) -> list[dict[str, Any]]:
        """OpenAI function-tool definition for computer use.

        Kimi via Fireworks understands standard function calling; we expose
        the computer_use surface as a single function with a structured
        action schema.
        """
        return [
            {
                "type": "function",
                "function": {
                    "name": "computer_use",
                    "description": (
                        f"Control the computer (display={display_width}x{display_height}). "
                        "Call this function to perform clicks, typing, scrolling, navigation, etc."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "action": {
                                "type": "string",
                                "enum": [
                                    "click",
                                    "double_click",
                                    "right_click",
                                    "type",
                                    "key",
                                    "scroll",
                                    "hscroll",
                                    "drag",
                                    "navigate",
                                    "wait",
                                    "terminate",
                                ],
                                "description": "The action to perform",
                            },
                            "x": {"type": "integer", "description": "X coordinate (0-999 normalized)"},
                            "y": {"type": "integer", "description": "Y coordinate (0-999 normalized)"},
                            "end_x": {"type": "integer", "description": "End X for drag"},
                            "end_y": {"type": "integer", "description": "End Y for drag"},
                            "text": {"type": "string", "description": "Text to type"},
                            "keys": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Keys to press (for key action)",
                            },
                            "url": {"type": "string", "description": "URL to navigate to"},
                            "scroll_x": {"type": "integer"},
                            "scroll_y": {"type": "integer"},
                            "button": {"type": "string", "enum": ["left", "right"]},
                            "status": {"type": "string"},
                            "result": {"type": "string"},
                        },
                        "required": ["action"],
                    },
                },
            }
        ]

    def _convert_input(self, input_messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Convert Lightcone-style input blocks into standard OpenAI messages."""
        messages: list[dict[str, Any]] = []
        current_role: str | None = None
        current_content: list[dict[str, Any]] = []

        for msg in input_messages:
            role = msg.get("role", "user")
            content = msg.get("content", [])

            # If it's already a string, pass through
            if isinstance(content, str):
                messages.append({"role": role, "content": content})
                continue

            # Convert Lightcone content blocks to OpenAI format
            openai_content: list[dict[str, Any]] = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type", "")
                if block_type == "input_text":
                    openai_content.append({"type": "text", "text": block.get("text", "")})
                elif block_type == "input_image":
                    image_url = block.get("image_url", "")
                    if image_url:
                        openai_content.append({
                            "type": "image_url",
                            "image_url": {"url": image_url, "detail": block.get("detail", "auto")},
                        })
                elif block_type == "text":
                    openai_content.append({"type": "text", "text": block.get("text", "")})
                elif block_type == "image_url":
                    openai_content.append({
                        "type": "image_url",
                        "image_url": {"url": block.get("url", ""), "detail": block.get("detail", "auto")},
                    })
                elif block_type == "computer_call_output":
                    # This is an assistant tool result — handled separately
                    pass

            # Handle computer_call_output blocks as tool responses
            tool_messages: list[dict[str, Any]] = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "computer_call_output":
                    call_id = block.get("call_id", "")
                    output = block.get("output", {})
                    if output.get("type") == "input_image":
                        tool_messages.append({
                            "role": "tool",
                            "tool_call_id": call_id,
                            "content": [
                                {
                                    "type": "image_url",
                                    "image_url": {"url": output.get("image_url", ""), "detail": "auto"},
                                }
                            ],
                        })
                    else:
                        tool_messages.append({
                            "role": "tool",
                            "tool_call_id": call_id,
                            "content": output.get("text", ""),
                        })

            if openai_content:
                messages.append({"role": role, "content": openai_content})
            messages.extend(tool_messages)

        return messages

    def create_response(
        self,
        input_messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        previous_response_id: str | None = None,
    ) -> ModelResponse:
        messages = self._convert_input(input_messages)

        # If there's a system prompt in the first user message, extract it
        if messages and messages[0].get("role") == "user":
            user_content = messages[0].get("content", [])
            if isinstance(user_content, list) and user_content and user_content[0].get("type") == "text":
                # Check if it starts with system-like instructions
                text = user_content[0].get("text", "")
                # Keep as-is — the system prompt is already embedded
                pass

        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "max_tokens": 4096,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        raw = self._client.chat.completions.create(**kwargs)

        message = raw.choices[0].message
        output: list[ResponseItem] = []

        # Text content
        if message.content:
            output.append(
                ResponseItem(
                    type="message",
                    content=[{"type": "text", "text": message.content}],
                )
            )

        # Tool calls → computer_calls
        for tool_call in getattr(message, "tool_calls", []) or []:
            action = ComputerCallAction.from_openai_tool(tool_call)
            output.append(
                ResponseItem(
                    type="computer_call",
                    call_id=getattr(tool_call, "id", ""),
                    action=action,
                )
            )

        return ModelResponse(id=raw.id, output=output, raw=raw)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def get_model_provider(provider_name: str | None = None) -> ModelProvider:
    """Return a ModelProvider instance based on env or argument.

    Args:
        provider_name: Explicit provider name. If None, reads CUA_MODEL_PROVIDER env var.

    Raises:
        ValueError: If the provider name is unknown.
    """
    name = (provider_name or os.getenv("CUA_MODEL_PROVIDER", "northstar")).lower().strip()

    if name in ("northstar", "tzafon", "lightcone"):
        return NorthstarProvider()
    if name in ("fireworks", "fw", "kimi"):
        return FireworksProvider()

    raise ValueError(
        f"Unknown CUA model provider: {name!r}. "
        f"Supported: northstar, fireworks. "
        f"Set CUA_MODEL_PROVIDER env var to choose."
    )
