"""Bounded path patterns shared by local and standalone guest file operations.

Keep this module standard-library-only: E2B embeds its source in the guest command.
"""

import fnmatch
import re

PATTERN_HINTS = {
    "invalid_glob": "Use *, ?, [abc], complete ** segments, or non-nested {a,b} alternatives; no backslashes. Limit brace expansion to 256 patterns and input to 16 KiB.",
    "invalid_regex": "Use a valid regular expression supported by this provider, or set regex=false for literal text. Portable patterns use literals, classes, groups, alternation, and quantifiers; avoid lookaround and backreferences.",
    "pattern_too_large": "Narrow the search pattern to at most 16 KiB of UTF-8 text.",
    "empty_pattern": "Provide nonempty search text, or use glob to list files.",
}


class PatternError(ValueError):
    def __init__(self, field: str, reason: str) -> None:
        super().__init__(reason)
        self.field = field
        self.reason = reason

    @property
    def details(self) -> dict[str, str]:
        return {"field": self.field, "reason": self.reason, "hint": PATTERN_HINTS[self.reason]}


def _class_end(pattern: str, start: int, field: str) -> int:
    position = start + 1
    if position < len(pattern) and pattern[position] in "!^":
        position += 1
    if position < len(pattern) and pattern[position] == "]":
        position += 1
    end = pattern.find("]", position)
    if end == -1 or "/" in pattern[start:end]:
        raise PatternError(field, "invalid_glob")
    body = pattern[start + 1 : end].lstrip("!^")
    for index in range(1, len(body) - 1):
        if body[index] == "-" and body[index - 1] > body[index + 1]:
            raise PatternError(field, "invalid_glob")
    return end


def _normalize_classes(pattern: str, field: str) -> str:
    parts: list[str] = []
    position = 0
    while position < len(pattern):
        if pattern[position] != "[":
            parts.append(pattern[position])
            position += 1
            continue
        end = _class_end(pattern, position, field)
        body = pattern[position + 1 : end]
        parts.append("[" + ("!" + body[1:] if body.startswith("^") else body) + "]")
        position = end + 1
    return "".join(parts)


def expand_glob(pattern: str, field: str = "pattern") -> list[str]:
    def invalid() -> PatternError:
        return PatternError(field, "invalid_glob")

    if not pattern.removeprefix("/") or len(pattern.encode()) > 16 * 1024 or "\\" in pattern:
        raise invalid()
    expanded = [""]
    position = 0
    while position < len(pattern):
        delimiter = re.search(r"[{}\[]", pattern[position:])
        if delimiter is None:
            expanded = [prefix + pattern[position:] for prefix in expanded]
            break
        end = position + delimiter.start()
        expanded = [prefix + pattern[position:end] for prefix in expanded]
        position = end
        if pattern[position] == "[":
            end = _class_end(pattern, position, field)
            expanded = [prefix + pattern[position : end + 1] for prefix in expanded]
            position = end + 1
            continue
        if pattern[position] == "}":
            raise invalid()
        end = pattern.find("}", position + 1)
        if end == -1:
            raise invalid()
        body = pattern[position + 1 : end]
        choices = body.split(",")
        if "{" in body or len(choices) < 2 or not all(choices) or len(expanded) * len(choices) > 256:
            raise invalid()
        expanded = [prefix + choice for prefix in expanded for choice in choices]
        position = end + 1
    for alternative in expanded:
        if any("**" in part and part != "**" for part in alternative.split("/")):
            raise invalid()
    return expanded


class PathPattern:
    def __init__(self, pattern: str, field: str = "pattern") -> None:
        self.alternatives = [
            (
                not alternative.startswith("/") and "/" not in alternative,
                tuple(
                    None if part == "**" else re.compile(fnmatch.translate(_normalize_classes(part, field)))
                    for part in alternative.removeprefix("/").split("/")
                ),
            )
            for alternative in expand_glob(pattern, field)
        ]

    def matches(self, path: str) -> bool:
        parts = path.split("/")
        for basename, pattern in self.alternatives:
            if basename:
                if pattern[0] is None or pattern[0].fullmatch(parts[-1]):
                    return True
                continue
            reachable = [True] + [False] * len(parts)
            for segment_index, segment in enumerate(pattern):
                following = [False] * (len(parts) + 1)
                if segment is None:
                    prefix = False
                    for index, matched in enumerate(reachable):
                        if segment_index == len(pattern) - 1 and len(pattern) > 1:
                            following[index] = prefix
                            prefix |= matched
                        else:
                            prefix |= matched
                            following[index] = prefix
                else:
                    for index, matched in enumerate(reachable[:-1]):
                        if matched and segment.fullmatch(parts[index]):
                            following[index + 1] = True
                reachable = following
            if reachable[-1]:
                return True
        return False


def content_pattern(pattern: str, regex: bool, case_sensitive: bool) -> re.Pattern[str]:
    if not pattern:
        raise PatternError("pattern", "empty_pattern")
    if len(pattern.encode()) > 16 * 1024:
        raise PatternError("pattern", "pattern_too_large")
    try:
        return re.compile(pattern if regex else re.escape(pattern), 0 if case_sensitive else re.IGNORECASE)
    except re.error as exc:
        raise PatternError("pattern", "invalid_regex") from exc
