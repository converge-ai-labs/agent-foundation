"""One slash-command grammar shared by dispatch, help, and completion."""

from __future__ import annotations

import shlex
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Command:
    name: str
    summary: str
    arguments: str = ""
    aliases: tuple[str, ...] = ()
    minimum: int = 0
    maximum: int = 0
    choices: tuple[str, ...] = ()
    busy: bool = False

    @property
    def usage(self) -> str:
        return f"/{self.name} {self.arguments}".rstrip()


COMMANDS = (
    Command("help", "Show command help and keyboard shortcuts.", "[command]", ("?",), maximum=1, busy=True),
    Command(
        "mode",
        "Switch output detail without changing execution.",
        "[concise|detailed]",
        maximum=1,
        choices=("concise", "detailed"),
        busy=True,
    ),
    Command("status", "Show model, context, environment, and session details.", busy=True),
    Command("setup", "Configure a provider and explicit context settings; existing files are preserved."),
    Command("model", "List configured models or select one for this session.", "[model-id]", maximum=1),
    Command(
        "thinking",
        "Show or change reasoning effort for subsequent turns.",
        "[level]",
        maximum=1,
        choices=("default", "low", "medium", "high", "xhigh"),
    ),
    Command(
        "environment",
        "Show or select execution permissions for subsequent turns.",
        "[mode]",
        maximum=1,
        choices=("full-control", "sandbox"),
    ),
    Command("new", "Start a fresh session; keep all saved history."),
    Command("resume", "List recent sessions in this workspace or resume one.", "[session-id]", maximum=1),
    Command("history", "Print retained history for the current session.", "[cursor]", maximum=1),
    Command(
        "login",
        "Authenticate a subscription account (device flow).",
        "[codex|grok]",
        maximum=1,
        choices=("codex", "grok"),
    ),
    Command("config", "Show configuration paths and effective precedence."),
    Command("approve", "Approve a pending tool request once.", "request-id", minimum=1, maximum=1),
    Command("deny", "Deny a pending tool request.", "request-id", minimum=1, maximum=1),
    Command("result", "Supply a JSON result for a pending external tool.", "request-id JSON", minimum=2, maximum=2),
    Command(
        "review", "Inspect a pending request against the selected continuation.", "request-id", minimum=1, maximum=1
    ),
    Command("cancel", "Cancel active work and wait for durable cleanup.", busy=True),
    Command("quit", "Exit; cancel active work and close the App.", aliases=("exit",), busy=True),
)


@dataclass(frozen=True, slots=True)
class Invocation:
    command: Command
    arguments: tuple[str, ...]


class CommandRegistry:
    def __init__(self, commands: tuple[Command, ...] = COMMANDS) -> None:
        self.commands = commands
        self._index: dict[str, Command] = {}
        for command in commands:
            for name in (command.name, *command.aliases):
                if name in self._index:
                    raise ValueError(f"Duplicate slash command: {name}")
                self._index[name] = command

    def parse(self, text: str, *, busy: bool = False) -> Invocation:
        tokens = shlex.split(text.removeprefix("/"))
        command = self._index.get(tokens[0]) if tokens else None
        if command is None:
            raise ValueError("Unknown command. Use /help; slash input is never sent to the model.")
        arguments = tuple(tokens[1:])
        if not command.minimum <= len(arguments) <= command.maximum:
            raise ValueError(f"Usage: {command.usage}")
        if arguments and command.choices and arguments[0] not in command.choices:
            raise ValueError(f"Usage: {command.usage}. Choices: {', '.join(command.choices)}")
        if busy and not command.busy:
            raise ValueError(f"{command.usage} is unavailable while working. Use /cancel first.")
        return Invocation(command, arguments)

    def help(self, name: str | None = None) -> str:
        commands = self.commands
        if name is not None:
            selected = self._index.get(name.removeprefix("/"))
            if selected is None:
                raise ValueError(f"Unknown command: {name}")
            commands = (selected,)
        lines = [f"{command.usage:36} {command.summary}" for command in commands]
        if name is None:
            lines += [
                "",
                "Enter: send   Alt+Enter: newline   Tab: complete   Ctrl+C: clear/cancel   Ctrl+D: exit",
                "Bracketed multiline paste stays in the draft until Enter. /mode works during a run.",
            ]
        return "\n".join(lines)

    def completions(self, text: str) -> tuple[tuple[str, str], ...]:
        if not text.startswith("/"):
            return ()
        head, separator, tail = text[1:].partition(" ")
        if not separator:
            return tuple(
                (f"/{item.name}", item.summary)
                for item in self.commands
                if any(name.startswith(head) for name in (item.name, *item.aliases))
            )
        command = self._index.get(head)
        if command is None:
            return ()
        return tuple((value, command.summary) for value in command.choices if value.startswith(tail))
