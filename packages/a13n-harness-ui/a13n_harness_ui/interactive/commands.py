"""One slash-command grammar shared by dispatch, help, and completion."""

from __future__ import annotations

import shlex
from dataclasses import dataclass

from a13n_harness_ui.skill_input import skill_tokens
from a13n_harness_ui.surfaces import SkillCatalogView, SkillReference
from a13n_harness_ui.thread_files import ComposerInput


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
        "Choose the TUI theme.",
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
    Command("attach", "Attach a file or image to the current draft.", "path", minimum=1, maximum=1, busy=True),
    Command("paste-image", "Read clipboard images explicitly.", busy=True),
    Command("recover", "Restore an unsent prompt."),
    Command(
        "goal",
        "Work toward a goal with completion audits and bounded continuations.",
        "task description",
        minimum=1,
        maximum=1,
        raw_tail=True,
    ),
    Command("status", "Show Model, context, Environment, and subscription usage.", busy=True),
    Command("ps", "Inspect observed background processes and their last reported status.", busy=True),
    Command(
        "subagents",
        "Inspect this conversation's child executions and retained output.",
        "[execution-id|next]",
        maximum=1,
        busy=True,
    ),
    Command(
        "usage",
        "Show recorded Thread usage; subscription shows Codex plan limits; "
        "reset redeems a Codex reset credit after confirmation.",
        "[details|subscription|reset]",
        maximum=1,
        choices=("details", "subscription", "reset"),
        busy=True,
    ),
    Command(
        "steer",
        "Steer the active Run with additional input.",
        "message",
        minimum=1,
        maximum=1,
        busy=True,
        raw_tail=True,
    ),
    Command("import", "Preview and optionally enable external subagents with parent inheritance."),
    Command(
        "agent",
        "Switch Agent, including its Model, instructions, and tools.",
        "[agent-id]",
        maximum=1,
    ),
    Command(
        "model",
        "Override the Model until you quit and remember it for the Project; default clears the override and the "
        "remembered choice; defaults configures global media understanding with Save/Cancel.",
        "[model-id|default|defaults]",
        maximum=1,
    ),
    Command(
        "fast",
        "Select Fast or Ultrafast until you quit, without saving configuration.",
        "[on|off|ultrafast|reset]",
        maximum=1,
        choices=("on", "off", "ultrafast", "reset"),
    ),
    Command(
        "pro",
        "Select Pro reasoning mode; off selects Standard, reset inherits the Model.",
        "[on|off|reset]",
        maximum=1,
        choices=("on", "off", "reset"),
    ),
    Command(
        "thinking",
        "Show or change reasoning effort for subsequent turns.",
        "[level]",
        maximum=1,
        choices=(),
    ),
    Command(
        "environment",
        "Show or select the Environment mode (Full Control or Sandbox) for subsequent turns.",
        "[mode]",
        maximum=1,
        choices=("full-control", "sandbox"),
    ),
    Command("new", "Start a new conversation; keep all saved history."),
    Command(
        "resume",
        "Search, preview, and name saved conversations, or resume one by Thread ID.",
        "[thread-id]",
        maximum=1,
    ),
    Command("history", "Browse retained messages (Ctrl+T)."),
    Command("notes", "Show saved notes with full contents within display budgets."),
    Command("config", "Find your configuration files."),
    Command(
        "review", "Inspect a pending request against the selected continuation.", "request-id", minimum=1, maximum=1
    ),
    Command("cancel", "Stop the active Run.", busy=True),
    Command("quit", "Quit the TUI; the saved conversation stays resumable.", aliases=("exit",), busy=True),
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
        self.thinking_choices: tuple[tuple[str, str], ...] = ()
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
            if any(char.isspace() for char in item.name):
                continue
            self.skills[item.name] = (
                item.description,
                SkillReference(catalog_id=catalog.catalog_id, item_id=item.item_id, name=item.name),
            )

    def skill_references(self, text: str | ComposerInput) -> tuple[SkillReference, ...]:
        parts = (text,) if isinstance(text, str) else text.parts
        names = dict.fromkeys(name for part in parts if isinstance(part, str) for name, _, _ in skill_tokens(part))
        return tuple(self.skills[name][1] for name in names if name in self.skills)

    def lookup(self, text: str) -> Command | None:
        """Match a complete command name or alias, never a prose prefix."""
        head = text.removeprefix("/").split(maxsplit=1)
        return self._index.get(head[0]) if head else None

    def parse(self, text: str, *, busy: bool = False) -> Invocation:
        source = text.removeprefix("/")
        command = self.lookup(text)
        if command is None:
            raise ValueError("Unknown command. Use /help for available commands.")
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
        lines = ["## Commands" if name is None else f"## /{commands[0].name}", ""]
        lines.extend(f"`{command.usage}` — {command.summary}" for command in commands)
        if name is not None and commands[0].aliases:
            lines += ["", "Aliases: " + ", ".join(f"`/{alias}`" for alias in commands[0].aliases)]
        if name is None:
            lines += [
                "",
                "### Shortcuts",
                "",
                "`Enter` send or steer · `Ctrl+J` / `Alt+Enter` newline · `Tab` complete",
                "`Ctrl+C` stop / twice to exit · `Ctrl+D` exit · `Ctrl+O` details",
                "`Ctrl+V` paste image · `Alt+E` expand paste · `PgUp/PgDn` scroll · `Ctrl+End` latest · `/mouse off` copy",
                "`Ctrl+T` browse retained messages · `$` complete available Skills · `/` commands",
                "",
                "Add Agents with `a13n-harness-ui add agent`. `/model` remembers a Model for the Project; "
                "`/model default` returns to the Agent's Model.",
            ]
        if name is None or commands[0].name in {"paste-image", "attach"}:
            lines += [
                "",
                "Image paste reads the clipboard on the host running this TUI. Over SSH, it does not "
                "read your local computer's clipboard. Text paste still works through your terminal "
                "(Cmd+V on macOS). Upload images to the remote host, then `/attach <remote-path>`; "
                "pasting a local file path does not upload the file.",
            ]
        return "\n".join(lines)

    def completions(self, text: str) -> tuple[tuple[str, str], ...]:
        token = text.split()[-1] if text and not text[-1].isspace() else ""
        if token.startswith("$"):
            return tuple(
                (f"${name}", " ".join(item[0].split())[:160])
                for name, item in sorted(self.skills.items())
                if name.startswith(token[1:])
            )
        if not text.startswith("/"):
            return ()
        head, separator, tail = text[1:].partition(" ")
        if not separator:
            return tuple(
                (f"/{head if head in item.aliases else item.name}", item.summary)
                for item in self.commands
                if any(name.startswith(head) for name in (item.name, *item.aliases))
            )
        command = self._index.get(head)
        if command is None:
            return ()
        if command.name == "thinking":
            return tuple(item for item in self.thinking_choices if item[0].startswith(tail))
        return tuple((value, command.summary) for value in command.choices if value.startswith(tail))
