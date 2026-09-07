"""Small inline selectors; no application imports or execution authority."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class Choice:
    value: str
    label: str
    description: str = ""


@dataclass(slots=True)
class Selection:
    choices: tuple[Choice, ...]
    cursor: int = -1
    multiple: bool = False
    checked: set[int] = field(default_factory=set)

    def move(self, offset: int) -> None:
        if not offset:
            return
        if self.cursor < 0 and offset < 0:
            self.cursor = 0
        self.cursor = (self.cursor + offset) % len(self.choices)

    def toggle(self) -> None:
        if self.cursor < 0:
            self.cursor = 0
        if self.cursor in self.checked:
            self.checked.remove(self.cursor)
        else:
            self.checked.add(self.cursor)

    def answer(self) -> str:
        if self.multiple and self.checked:
            return ",".join(str(index + 1) for index in sorted(self.checked))
        if self.cursor < 0:
            raise ValueError("Choose an option with Up/Down, type a number, or enter your own answer.")
        return str(self.cursor + 1)

    def lines(self, *, max_choices: int = 8, descriptions: bool = True) -> list[tuple[str, str]]:
        result: list[tuple[str, str]] = []
        max_choices = max(1, max_choices)
        start = max(0, self.cursor - max_choices + 1)
        for index, choice in enumerate(self.choices[start : start + max_choices], start):
            focused = index == self.cursor
            marker = "[x]" if index in self.checked else "[ ]" if self.multiple else ">" if focused else " "
            result.append(
                ("class:selection.focus" if focused else "class:selection", f" {marker} {index + 1}. {choice.label}")
            )
            if choice.description and descriptions:
                result.append(("class:selection.description", f" — {choice.description}"))
            result.append(("", "\n"))
        hint = "Up/Down select · Space toggle · Enter confirm" if self.multiple else "Up/Down select · Enter confirm"
        result.append(("class:selection.hint", hint + " · type to answer · Esc back/cancel\n"))
        return result


def resolve_choice(text: str, choices: tuple[str, ...], *, multiple: bool = False) -> str | tuple[str, ...]:
    """Numeric-looking answers are validated, never silently turned into free text."""
    value = text.strip()
    if not value:
        raise ValueError("An answer is required.")
    numbers = [part.strip() for part in value.split(",")]
    if all(part.isdecimal() for part in numbers):
        indices = [int(part) - 1 for part in numbers]
        if any(index < 0 or index >= len(choices) for index in indices):
            raise ValueError(f"Choose a number from 1 to {len(choices)}, or enter a text answer.")
        if not multiple and len(indices) != 1:
            raise ValueError("This question accepts one option.")
        if len(set(indices)) != len(indices):
            raise ValueError("Choose each option only once.")
        selected = tuple(choices[index] for index in indices)
        return selected if multiple else selected[0]
    return value
