"""Terminal form drafts for live MCP input, independent of deferred decisions."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from a13n_harness.mcp_inputs import McpInputResponse, validate_field

from a13n_harness_ui.mcp_runtime.inputs import McpInputRequestView

from .selection import resolve_choice


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
        if self.request.mode == "permission" and self.request.permission is not None:
            detail = self.request.permission
            source = self.request.server_name or self.request.server_id
            lines = [f"Permission requested ({source})"]
            if self.request.message:
                lines.append(self.request.message)
            if detail.command:
                lines.append(f"Command: {detail.command}")
            if detail.cwd:
                lines.append(f"Working directory: {detail.cwd}")
            if detail.locations:
                lines.append("Locations: " + ", ".join(detail.locations))
            if detail.environment_keys:
                lines.append("Environment variables: " + ", ".join(detail.environment_keys))
            lines.append("\n".join(f"{index}. {option.name}" for index, option in enumerate(detail.options, 1)))
            lines.append("Choose a number or option name; or decline / cancel.")
            return "\n".join(lines)
        if self.request.mode == "question":
            question = self.request.questions[self.position]
            options = "\n".join(
                f"{index}. {option.label}\n   {option.description}" for index, option in enumerate(question.options, 1)
            )
            return (
                f"{question.header} · {self.position + 1}/{len(self.request.questions)}\n"
                f"{question.question}\n{options}\nChoose a number or type your own answer; or decline / cancel."
            )
        if self.request.mode == "url":
            return f"External destination ({self.request.url_host}): {self.request.url}\nAfter completing the action, type accept; or decline / cancel."
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
        if self.request.mode == "permission" and self.request.permission is not None:
            options = self.request.permission.options
            stripped = text.strip()
            for option in options:
                if stripped == option.option_id:
                    return McpInputResponse(action="accept", content={"option": option.option_id})
            chosen = resolve_choice(stripped, tuple(option.name for option in options), multiple=False)
            assert isinstance(chosen, str)
            matched = next(option for option in options if option.name == chosen)
            return McpInputResponse(action="accept", content={"option": matched.option_id})
        if self.request.mode == "question":
            question = self.request.questions[self.position]
            answer = resolve_choice(
                text, tuple(option.label for option in question.options), multiple=question.multi_select
            )
            self.content[question.question] = list(answer) if isinstance(answer, tuple) else answer
            if self.position + 1 == len(self.request.questions):
                return McpInputResponse(action="accept", content={"answers": self.content})
            self.position += 1
            return None
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
            validate_field(schema, value)
            self.content[name] = value
        self.position += 1
        return None
