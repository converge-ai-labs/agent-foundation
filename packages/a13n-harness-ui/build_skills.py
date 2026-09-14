"""Prepare release-owned Skills from the existing documentation, without importing the UI."""

from __future__ import annotations

import posixpath
import shutil
from collections.abc import Iterator
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urljoin, urlsplit

import yaml
from markdown_it import MarkdownIt

SKILL_NAME = "harness-ui-configuration"
DOC_PREFIX = "a13n-harness-ui/"
BUNDLE_PATH = Path("a13n_harness_ui/assets/builtin_skills")


def _navigation(value: object, groups: tuple[str, ...] = ()) -> Iterator[tuple[tuple[str, ...], str, str]]:
    if isinstance(value, list):
        for child in value:
            yield from _navigation(child, groups)
    elif isinstance(value, dict):
        for label, child in value.items():
            if not isinstance(label, str):
                raise ValueError("Documentation navigation labels must be strings")
            if isinstance(child, str):
                if child.startswith(DOC_PREFIX):
                    yield groups, label, child
            else:
                yield from _navigation(child, (*groups, label))


def _headings(content: str) -> list[tuple[int, str, int, int]]:
    """Return heading level, label, and one-based inclusive section boundaries."""
    tokens = MarkdownIt().parse(content)
    headings: list[tuple[int, str, int]] = []
    for index, token in enumerate(tokens):
        if token.type == "heading_open" and token.map is not None:
            headings.append((int(token.tag[1:]), tokens[index + 1].content, token.map[0] + 1))
    result = []
    for index, (level, title, start) in enumerate(headings):
        end = next(
            (line - 1 for next_level, _, line in headings[index + 1 :] if next_level <= level),
            len(content.splitlines()),
        )
        result.append((level, title, start, end))
    return result


def _references(content: str) -> Iterator[str]:
    for token in MarkdownIt().parse(content):
        for child in token.children or ():
            if child.type in {"link_open", "image"}:
                value = child.attrGet("href" if child.type == "link_open" else "src")
                if isinstance(value, str) and value:
                    yield value


def _online_url(site_url: str, path: str, fragment: str) -> str:
    page = path.removesuffix(".md")
    if page.endswith("/index"):
        page = page.removesuffix("index")
    elif page == "index":
        page = ""
    elif path.endswith(".md"):
        page += "/"
    return urljoin(site_url.rstrip("/") + "/", page) + (f"#{fragment}" if fragment else "")


def build_skills(package_root: Path, repository_root: Path) -> Path:
    """Generate one complete bundle; repository docs remain the only content source."""
    config = yaml.safe_load((repository_root / "mkdocs.yml").read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("mkdocs.yml must contain a mapping")
    site_url = config.get("site_url")
    if not isinstance(site_url, str):
        raise ValueError("mkdocs.yml must declare site_url for unbundled references")
    docs_root = repository_root / "docs"
    source_root = docs_root / DOC_PREFIX
    pages = list(_navigation(config.get("nav")))
    if not pages:
        raise ValueError("MkDocs navigation contains no Harness UI documentation")
    listed = {path for _, _, path in pages}
    pages.extend(
        (("Additional documentation",), path.stem, path.relative_to(docs_root).as_posix())
        for path in sorted(source_root.rglob("*.md"))
        if path.relative_to(docs_root).as_posix() not in listed
    )
    template = (package_root / "a13n_harness_ui/assets/configuration_skill.md").read_text(encoding="utf-8")
    overview = [template.rstrip(), "", "## Documentation map", ""]
    detail = [
        "# Documentation navigation",
        "",
        "File paths below are relative to the Skill directory, not this index. "
        "Line ranges are one-based and inclusive in the bundled files. "
        "Read the relevant parent section when an example depends on surrounding instructions.",
        "",
    ]
    external: set[tuple[str, str, str]] = set()
    previous_group: tuple[str, ...] | None = None
    for groups, label, source_path in pages:
        normalized = PurePosixPath(source_path)
        if ".." in normalized.parts or not source_path.startswith(DOC_PREFIX):
            raise ValueError(f"Invalid bundled documentation path: {source_path}")
        source = docs_root / source_path
        content = source.read_text(encoding="utf-8")
        relative = source.relative_to(source_root).as_posix()
        target = f"docs/{relative}"
        if groups != previous_group:
            overview.extend([f"### {' / '.join(groups) or 'Documentation'}", ""])
            previous_group = groups
        overview.append(f"- [{label}]({target})")
        headings = _headings(content)
        topics = [title for level, title, _, _ in headings if level == 2]
        if topics:
            overview.append(f"  Topics: {'; '.join(topics)}.")
        overview.append("")
        detail.extend([f"## {label}", "", f"File: `{target}`", ""])
        for level, title, start, end in headings:
            if level in {2, 3}:
                indent = "  " if level == 3 else ""
                detail.append(f"{indent}- {title} — lines {start}-{end}")
        detail.append("")
        for reference in _references(content):
            parsed = urlsplit(reference)
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue
            resolved = posixpath.normpath(posixpath.join(posixpath.dirname(source_path), unquote(parsed.path)))
            if resolved.startswith(DOC_PREFIX):
                if not (docs_root / resolved).is_file():
                    raise ValueError(f"Missing bundled reference in {source_path}: {reference}")
            else:
                external.add((target, reference, _online_url(site_url, resolved, parsed.fragment)))
    if external:
        detail.extend(
            [
                "## Online references outside this bundle",
                "",
                "The source documents retain their original relative links. Targets outside the Harness UI "
                "documentation are not bundled; use the online counterparts below only when needed. "
                "Online documentation may describe a different release.",
                "",
            ]
        )
        for source, reference, online in sorted(external):
            detail.append(f"- From `{source}`: `{reference}` → <{online}>")
        detail.append("")
    output = package_root / BUNDLE_PATH / SKILL_NAME
    # All navigation and link checks complete before replacing generated content.
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    shutil.copytree(source_root, output / "docs")
    (output / "references").mkdir()
    (output / "SKILL.md").write_text("\n".join(overview).rstrip() + "\n", encoding="utf-8")
    (output / "references/navigation.md").write_text("\n".join(detail), encoding="utf-8")
    return output


def prepare_skills(package_root: Path) -> Path:
    """Build from a source checkout, or use the self-contained sdist contents."""
    repository_root = package_root.parent.parent
    if (repository_root / "mkdocs.yml").is_file():
        return build_skills(package_root, repository_root)
    output = package_root / BUNDLE_PATH / SKILL_NAME
    if not all((output / path).is_file() for path in ("SKILL.md", "references/navigation.md")):
        raise RuntimeError("Harness UI built-in Skills are missing from the source distribution")
    if not (output / "docs").is_dir():
        raise RuntimeError("Harness UI bundled documentation is missing from the source distribution")
    return output


if __name__ == "__main__":
    print(prepare_skills(Path(__file__).resolve().parent))
