"""Terminal form drafts for live MCP input, independent of deferred decisions."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from a13n_harness_ui.mcp_runtime.inputs import McpInputRequestView, McpInputResponse


@dataclass
class McpInteraction:
    request: McpInputRequestView
    content: dict[str, Any] = field(default_factory=dict)
    position: int = 0

    @property
    def fields(self) -> tuple[tuple[str, dict[str, Any]], ...]:
        properties = (self.request.schema_ or {}).get("properties", {})
        if not isinstance(properties, dict):
            return ()
        return tuple((name, value) for name, value in properties.items() if isinstance(value, dict))

    @property
    def required(self) -> tuple[str, ...]:
        required = (self.request.schema_ or {}).get("required", [])
        return tuple(item for item in required if isinstance(item, str)) if isinstance(required, list) else ()

    def prompt(self) -> str:
        if self.request.mode == "url":
            return f"Open in your browser: {self.request.url}\nAfter completing the action, type accept; or decline / cancel."
        if self.position >= len(self.fields):
            return f"Confirm response: {json.dumps(self.content, ensure_ascii=False)}\nType accept, decline, or cancel."
        name, schema = self.fields[self.position]
        required = name in self.required
        return (
            f"{schema.get('title', name)} ({schema.get('type')}{'; required' if required else '; optional, type skip to omit'}): "
            f"{schema.get('description', '')}\n"
            f"{json.dumps(schema, ensure_ascii=False)}\n"
            "Enter a value (text for strings; JSON for other types), or decline / cancel."
        )

    def answer(self, text: str) -> McpInputResponse | None:
        if text.strip() in ("decline", "cancel"):
            return McpInputResponse(action="decline" if text.strip() == "decline" else "cancel")
        if self.request.mode == "url" or self.position >= len(self.fields):
            if text.strip() != "accept":
                raise ValueError("Type accept, decline, or cancel.")
            return McpInputResponse(action="accept", content=self.content if self.request.mode == "form" else None)
        name, schema = self.fields[self.position]
        required = self.required
        if text.strip() == "skip":
            if name in required:
                raise ValueError("This field is required.")
        else:
            if schema.get("type") == "string":
                value = text
            else:
                try:
                    value = json.loads(text)
                except ValueError as exc:
                    raise ValueError("Enter a JSON number, boolean, or enum array.") from exc
            from jsonschema import Draft202012Validator, ValidationError

            try:
                Draft202012Validator(schema).validate(value)
            except ValidationError as exc:
                raise ValueError("The value does not match this field's schema.") from exc
            self.content[name] = value
        self.position += 1
        return None
