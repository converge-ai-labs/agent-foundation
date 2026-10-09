"""Generate the installed Service documentation index from canonical Markdown, without website tooling."""

import json
from pathlib import Path
from typing import Any

import yaml
from markdown_it import MarkdownIt

BUNDLE = Path("a13n_service/documentation.json")


def sections(source: str, path: str) -> list[dict[str, Any]]:
    lines = source.splitlines()
    start = 0
    title = Path(path).stem
    if lines and lines[0] == "---":
        end = lines.index("---", 1)
        metadata = yaml.safe_load("\n".join(lines[1:end]))
        title = metadata["title"]
        start = end + 1
    # Preserve source line offsets while excluding frontmatter from Markdown parsing.
    tokens = MarkdownIt().parse("\n".join([""] * start + lines[start:]))
    headings: list[tuple[int, int, str]] = []
    for index, token in enumerate(tokens):
        if token.type == "heading_open" and token.map is not None:
            headings.append((token.map[0], int(token.tag[1:]), tokens[index + 1].content))
    boundaries = [(start, 0, ""), *headings]
    result = []
    trail: list[tuple[int, str]] = []
    for index, (line, level, heading) in enumerate(boundaries):
        if heading:
            trail = [(depth, name) for depth, name in trail if depth < level]
            trail.append((level, heading))
        end = boundaries[index + 1][0] if index + 1 < len(boundaries) else len(lines)
        text = "\n".join(lines[line:end]).rstrip()
        if not text.strip():
            continue
        result.append(
            {
                "source": path,
                "language": "zh-CN" if path.endswith(".zh-CN.md") else "en",
                "title": title,
                "headings": [name for _, name in trail],
                "line_start": line + 1,
                "line_end": end,
                "text": text,
            }
        )
    return result


def validate_bundle(path: Path) -> None:
    bundle = json.loads(path.read_text(encoding="utf-8"))
    if bundle["format"] != 1 or not bundle["sections"]:
        raise ValueError("Invalid Service documentation bundle")
    for section in bundle["sections"]:
        if not (
            section["source"].startswith("docs/a13n-service/")
            and section["language"] in {"en", "zh-CN"}
            and isinstance(section["title"], str)
            and isinstance(section["text"], str)
            and isinstance(section["headings"], list)
            and 1 <= section["line_start"] <= section["line_end"]
        ):
            raise ValueError("Invalid Service documentation section")


def prepare_docs(package_root: Path) -> Path:
    repository = package_root.parent.parent
    source_root = repository / "docs/a13n-service"
    output = package_root / BUNDLE
    if (source_root / "meta.json").is_file():
        records = []
        for path in sorted(source_root.rglob("*.md")):
            records.extend(sections(path.read_text(encoding="utf-8"), path.relative_to(repository).as_posix()))
        output.write_text(
            json.dumps({"format": 1, "sections": records}, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8"
        )
    if not output.is_file():
        raise RuntimeError("Service documentation is missing from the source distribution")
    validate_bundle(output)
    return output


if __name__ == "__main__":
    print(prepare_docs(Path(__file__).resolve().parent))
