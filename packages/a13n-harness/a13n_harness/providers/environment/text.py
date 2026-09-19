"""Shared bounded text patch semantics for filesystem adapters."""

import re
from collections.abc import Iterator

from .models import EnvironmentError

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def iter_lf_lines(text: str) -> Iterator[str]:
    """Iterate LF-only lines without constructing per-file line collections."""
    start = 0
    while start < len(text):
        end = text.find("\n", start)
        if end < 0:
            yield text[start:]
            return
        yield text[start : end + 1]
        start = end + 1


def _split_lf_lines(text: str) -> list[str]:
    """Materialize bounded patch lines while preserving LF terminators."""
    return list(iter_lf_lines(text))


def apply_unified_diff(
    original: str,
    patch: str,
    *,
    max_result_bytes: int,
) -> tuple[str, int]:
    source = _split_lf_lines(original)
    patch_lines = _split_lf_lines(patch)
    output: list[str] = []
    source_index = 0
    index = 0
    hunks = 0
    while index < len(patch_lines):
        line = patch_lines[index]
        if line.startswith(("---", "+++")):
            index += 1
            continue
        match = _HUNK.match(line)
        if match is None:
            raise EnvironmentError("Unified diff contains an invalid hunk header.", code="environment_request_invalid")
        old_count = int(match.group(2) or "1")
        old_start = int(match.group(1)) - (1 if old_count else 0)
        if old_start < source_index or old_start > len(source):
            raise EnvironmentError("Unified diff hunk is out of range.", code="environment_conflict")
        output.extend(source[source_index:old_start])
        source_index = old_start
        index += 1
        hunks += 1
        while index < len(patch_lines) and not patch_lines[index].startswith("@@"):
            item = patch_lines[index]
            marker, content = item[:1], item[1:]
            no_newline = index + 1 < len(patch_lines) and patch_lines[index + 1].rstrip("\r\n") == (
                "\\ No newline at end of file"
            )
            if no_newline:
                if content.endswith("\r\n"):
                    content = content[:-2]
                elif content.endswith("\n"):
                    content = content[:-1]
                else:
                    raise EnvironmentError("Unified diff line is invalid.", code="environment_request_invalid")
            if marker == " ":
                if source_index >= len(source) or source[source_index] != content:
                    raise EnvironmentError("Unified diff context does not match.", code="environment_conflict")
                output.append(content)
                source_index += 1
            elif marker == "-":
                if source_index >= len(source) or source[source_index] != content:
                    raise EnvironmentError("Unified diff deletion does not match.", code="environment_conflict")
                source_index += 1
            elif marker == "+":
                output.append(content)
            else:
                raise EnvironmentError("Unified diff line is invalid.", code="environment_request_invalid")
            index += 2 if no_newline else 1
    if hunks == 0:
        raise EnvironmentError("Unified diff must contain at least one hunk.", code="environment_request_invalid")
    output.extend(source[source_index:])
    if sum(len(item.encode("utf-8")) for item in output) > max_result_bytes:
        raise EnvironmentError("Patch result exceeds configured limit.", code="environment_too_large")
    return "".join(output), hunks
