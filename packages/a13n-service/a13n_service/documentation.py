"""Bounded lexical search over documentation shipped with this Service release."""

import json
import re
from importlib.metadata import distribution
from typing import Annotated, Any, Literal

from pydantic import Field

MAX_TEXT = 3000


class Documents:
    def __init__(self) -> None:
        package = distribution("a13n-service")
        # Read the installed artifact, including for editable installs whose Python
        # modules live in the checkout but whose generated data belongs to the wheel.
        bundle = json.loads(package.locate_file("a13n_service/documentation.json").read_text(encoding="utf-8"))
        self.sections = bundle["sections"]
        self.version = package.version

    def search(
        self,
        query: Annotated[str, Field(min_length=1, max_length=256)],
        limit: Annotated[int, Field(ge=1, le=10)] = 5,
        language: Literal["en", "zh-CN"] = "en",
    ) -> dict[str, Any]:
        """Search bundled Service documentation, not the web. Return source-attributed Markdown excerpts.

        Use keywords, e.g. 'upload skill' or 'thread input'. Execution and file transfer examples require the
        caller's own HTTP/shell capability and credentials. Links outside docs/a13n-service/ are not bundled
        or version-matched; generated website API-reference pages are not in this Markdown bundle.
        """
        terms = set(re.findall(r"\w+", query.casefold()))
        ranked = []
        for section in self.sections:
            if section["language"] != language:
                continue
            title = section["title"].casefold()
            heading = " ".join(section["headings"]).casefold()
            text = section["text"].casefold()
            score = sum(12 * (term in title) + 6 * (term in heading) + (term in text) for term in terms)
            if score:
                ranked.append((score, section))
        ranked.sort(key=lambda item: (-item[0], item[1]["source"], item[1]["line_start"]))
        results = []
        for _, section in ranked[:limit]:
            text = section["text"]
            # For long sections, return the lines around the first match rather than an unrelated prefix.
            matches = [text.casefold().find(term) for term in terms if term in text.casefold()]
            first = min(matches, default=0)
            start = text.rfind("\n", 0, max(0, first - 200)) + 1 if first >= MAX_TEXT else 0
            excerpt = text[start : start + MAX_TEXT]
            results.append(
                {
                    **section,
                    "text": excerpt,
                    "excerpt_line_start": section["line_start"] + text[:start].count("\n"),
                    "truncated": start > 0 or start + len(excerpt) < len(text),
                }
            )
        return {
            "version": self.version,
            "language": language,
            "results": results,
            "total_matches": len(ranked),
            "truncated": len(ranked) > limit,
        }
