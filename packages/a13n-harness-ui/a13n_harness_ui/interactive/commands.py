"""One slash-command grammar shared by dispatch, help, and completion."""

from __future__ import annotations

import shlex
from dataclasses import dataclass

from a13n_harness_ui.surfaces import SkillCatalogView, SkillReference


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
    raw_tail: bool = False

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
    Command(
        "theme",
        "Select terminal colors without probing keyboard input.",
        "[auto|dark|light]",
        maximum=1,
        choices=("auto", "dark", "light"),
        busy=True,
    ),
    Command(
        "mouse",
        "Toggle wheel capture; off preserves native selection/copy.",
        "[on|off]",
        maximum=1,
        choices=("on", "off"),
        busy=True,
    ),
    Command("attach", "Attach an image to the current draft.", "path", minimum=1, maximum=1, busy=True),
    Command("paste-image", "Read clipboard images explicitly.", busy=True),
    Command(
        "remove", "Remove one image or all images from the current draft.", "index|all", minimum=1, maximum=1, busy=True
    ),
    Command("recover", "Restore the last prompt that failed before admission."),
    Command("status", "Show model, context, environment, and session details.", busy=True),
    Command(
        "steer",
        "Send additional text guidance to the current running receipt.",
        "message",
        minimum=1,
        maximum=1,
        busy=True,
        raw_tail=True,
    ),
    Command("import", "Preview and optionally enable external subagents with parent inheritance."),
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
    Command("config", "Show configuration paths and effective precedence."),
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
    source: str


class CommandRegistry:
    def __init__(self, commands: tuple[Command, ...] = COMMANDS) -> None:
        self.commands = commands
        self.skills: dict[str, tuple[str, SkillReference]] = {}
        self._index: dict[str, Command] = {}
        for command in commands:
            for name in (command.name, *command.aliases):
                if name in self._index:
                    raise ValueError(f"Duplicate slash command: {name}")
                self._index[name] = command

    def set_skills(self, catalog: SkillCatalogView | None) -> None:
        self.skills.clear()
        if catalog is None:
            return
        for item in catalog.items:
            if item.name in self._index or any(char.isspace() for char in item.name):
                continue
            self.skills[item.name] = (
                item.description,
                SkillReference(catalog_id=catalog.catalog_id, item_id=item.item_id, name=item.name),
            )

    def skill_references(self, text: str) -> tuple[SkillReference, ...]:
        head = text.split(maxsplit=1)[0] if text else ""
        item = self.skills.get(head[1:]) if head.startswith("/") else None
        return (item[1],) if item is not None else ()

    def parse(self, text: str, *, busy: bool = False) -> Invocation:
        source = text.removeprefix("/")
        head = source.split(maxsplit=1)
        command = self._index.get(head[0]) if head else None
        if command is None:
            raise ValueError("Unknown command. Use /help; slash input is never sent to the model.")
        if command.raw_tail:
            # Text and JSON are values, not shell syntax. Preserve the final
            # argument byte-for-byte, including quotes, backslashes and newlines.
            tokens = source.split(maxsplit=command.maximum)
        else:
            lexer = shlex.shlex(source, posix=True)
            lexer.whitespace_split = True
            lexer.commenters = ""
            lexer.escape = ""  # Native Windows paths are not POSIX shell escapes.
            tokens = list(lexer)
        arguments = tuple(tokens[1:])
        if not command.minimum <= len(arguments) <= command.maximum:
            raise ValueError(f"Usage: {command.usage}")
        if arguments and command.choices and arguments[0] not in command.choices:
            raise ValueError(f"Usage: {command.usage}. Choices: {', '.join(command.choices)}")
        if busy and not command.busy:
            raise ValueError(f"{command.usage} is unavailable while working. Use /cancel first.")
        return Invocation(command, arguments, text)

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
                "Enter: send   Alt+Enter: newline   Tab: complete   Ctrl+C: cancel / twice to exit   Ctrl+D: exit",
                "Enter adds text guidance during a run. Bracketed multiline paste stays a draft until Enter.",
                "Quote paths containing spaces. Decisions use the selection panel. Sign in with a13n-harness-ui login; configure with a13n-harness-ui setup.",
                "Ctrl+V / Alt+V: paste image   PgUp/PgDn: scroll   Ctrl+End: latest   /mouse off: native copy",
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
            ) + tuple((f"/{name}", f"Skill · {item[0]}") for name, item in self.skills.items() if name.startswith(head))
        command = self._index.get(head)
        if command is None:
            return ()
        return tuple((value, command.summary) for value in command.choices if value.startswith(tail))
