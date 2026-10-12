"""Standard-library bounded text scanner shared with the E2B guest."""

import re
from collections import deque
from pathlib import Path
from typing import cast


def search_text_file(
    path: Path,
    needle: str,
    regex: re.Pattern[str] | None,
    case_sensitive: bool,
    skip: int,
    limit: int,
    max_matches_per_file: int | None,
    context_lines: int,
    max_line_length: int,
    max_line_bytes: int,
) -> tuple[list[tuple[int, str, bool, str, int]], int] | None:
    """Scan one UTF-8 file incrementally and emit bounded matches with inline context."""
    selected: list[tuple[int, str, bool, str, int]] = []
    before: deque[tuple[int, str]] = deque(maxlen=context_lines)
    pending: list[dict[str, object]] = []
    matched = 0
    stop_after_context = False
    with path.open("rb") as file:
        line_number = 0
        while raw := file.readline(max_line_bytes + 2):
            line_number += 1
            terminated = raw.endswith(b"\n")
            content = raw[:-1] if terminated else raw
            if len(content) > max_line_bytes:
                raise OverflowError("Text search line exceeds value limit.")
            if b"\x00" in content:
                return None
            try:
                line = content.decode("utf-8", errors="strict")
            except UnicodeDecodeError:
                return None
            rendered = line[:max_line_length] + ("\n" if terminated else "")

            remaining_pending: list[dict[str, object]] = []
            for item in pending:
                context = cast(list[str], item["context"])
                context.append(rendered)
                remaining = cast(int, item["remaining"]) - 1
                if remaining == 0:
                    selected.append(
                        (
                            cast(int, item["line"]),
                            cast(str, item["text"]),
                            cast(bool, item["truncated"]),
                            "".join(context),
                            cast(int, item["context_start_line"]),
                        )
                    )
                else:
                    item["remaining"] = remaining
                    remaining_pending.append(item)
            pending = remaining_pending
            if stop_after_context and not pending:
                break

            candidate = line if case_sensitive else line.casefold()
            is_match = regex.search(line) is not None if regex is not None else needle in candidate
            within_file_limit = max_matches_per_file is None or matched < max_matches_per_file
            if is_match and within_file_limit:
                if matched >= skip and len(selected) + len(pending) < limit:
                    context = [text for _, text in before]
                    context.append(rendered)
                    item = {
                        "line": line_number,
                        "text": line[:max_line_length],
                        "truncated": len(line) > max_line_length,
                        "context": context,
                        "context_start_line": before[0][0] if before else line_number,
                        "remaining": context_lines,
                    }
                    if context_lines == 0:
                        selected.append(
                            (
                                line_number,
                                cast(str, item["text"]),
                                cast(bool, item["truncated"]),
                                "".join(context),
                                cast(int, item["context_start_line"]),
                            )
                        )
                    else:
                        pending.append(item)
                matched += 1
                if len(selected) + len(pending) >= limit or (
                    max_matches_per_file is not None and matched >= max_matches_per_file
                ):
                    stop_after_context = True
                if stop_after_context and not pending:
                    break
            before.append((line_number, rendered))
        for item in pending:
            selected.append(
                (
                    cast(int, item["line"]),
                    cast(str, item["text"]),
                    cast(bool, item["truncated"]),
                    "".join(cast(list[str], item["context"])),
                    cast(int, item["context_start_line"]),
                )
            )
    return selected, matched
