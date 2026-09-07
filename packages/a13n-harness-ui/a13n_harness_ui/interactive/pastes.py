"""Terminal-only paste folding. The submitted value is always expanded text."""

from __future__ import annotations


class PendingPastes:
    def __init__(self) -> None:
        self._values: dict[str, str] = {}
        self._next = 0

    def insert(self, text: str) -> str:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        if len(text) <= 1000:
            return text
        self._next += 1
        marker = f"[Pasted text #{self._next}: {len(text)} chars]"
        self._values[marker] = text
        return marker

    def expand(self, text: str) -> str:
        # One pass: pasted data that happens to contain another marker is literal.
        if not self._values:
            return text
        import re

        pattern = "|".join(re.escape(marker) for marker in self._values)
        result = re.sub(pattern, lambda match: self._values[match[0]], text)
        return result

    def edit(self, text: str, position: int) -> tuple[str, int] | None:
        for marker, value in self._values.items():
            start = text.find(marker)
            if start >= 0 and start < position < start + len(marker):
                return text[:start] + value + text[start + len(marker) :], start + min(position - start, len(value))
        return None

    def retain(self, texts: tuple[str, ...]) -> None:
        self._values = {marker: text for marker, text in self._values.items() if any(marker in t for t in texts)}

    def deletion(self, text: str, position: int, *, backward: bool) -> int:
        for marker in self._values:
            if backward and text[:position].endswith(marker):
                return len(marker)
            if not backward and text[position:].startswith(marker):
                return len(marker)
        return 1
