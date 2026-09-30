"""Documentation source conventions and content regression checks.

The site build in frontend/apps/a13n-docs checks rendering, links, and anchors.
"""

from __future__ import annotations

import json
import os
import re
import runpy
from pathlib import Path

import a13n_logging
import click
import pytest
import yaml
from a13n_envd_client.eip.v1 import METHODS
from a13n_harness_ui.cli import cli
from a13n_harness_ui.content_plugins import ContentPluginManifest, ContentPluginMarketplace
from a13n_harness_ui.interactive.commands import COMMANDS
from markdown_it import MarkdownIt

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
# The home page is the site's landing page rather than a navigation entry.
HOME = DOCS / "index.mdx"


def _pages(locale: str | None = None, folder: Path = DOCS) -> list[Path]:
    return sorted(
        path
        for path in folder.rglob("*")
        if path.suffix in {".md", ".mdx"} and (locale is None or (".zh-CN." in path.name) == (locale == "zh-CN"))
    )


def _front_matter(path: Path) -> tuple[dict[str, object], str]:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{path} has no front matter"
    header, body = text[4:].split("\n---\n", 1)
    data = yaml.safe_load(header)
    assert isinstance(data, dict), path
    return data, body


def _navigation(folder: Path, locale: str = "en") -> list[Path]:
    """Pages reachable from a folder's meta.json, following Fumadocs page references."""
    suffix = ".zh-CN" if locale == "zh-CN" else ""
    meta = folder / f"meta{suffix}.json"
    if not meta.is_file():
        return _pages(locale, folder)
    pages: list[Path] = []
    for entry in json.loads(meta.read_text(encoding="utf-8"))["pages"]:
        if entry.startswith("---") or entry.startswith("["):
            continue
        # `...folder` lists a folder's pages in place; paths may leave the folder.
        target = Path(os.path.normpath(folder / entry.removeprefix("...")))
        if target.is_dir():
            pages.extend(_navigation(target, locale))
        else:
            matches = [
                Path(f"{target}{suffix}{extension}")
                for extension in (".md", ".mdx")
                if Path(f"{target}{suffix}{extension}").is_file()
            ]
            assert matches, f"{meta.relative_to(ROOT)} lists missing page {entry}"
            pages.extend(matches)
    return pages


def test_docs_contains_only_pages_and_navigation() -> None:
    other = [
        path
        for path in DOCS.rglob("*")
        if path.is_file() and path not in _pages() and path.name not in {"meta.json", "meta.zh-CN.json"}
    ]
    assert not other, "docs/ holds Markdown pages and meta.json navigation only"


def test_every_page_has_title_and_description_front_matter() -> None:
    for path in _pages():
        data, body = _front_matter(path)
        assert isinstance(data.get("title"), str) and data["title"], path
        assert isinstance(data.get("description"), str) and data["description"], path
        headings = [token.tag for token in MarkdownIt().parse(body) if token.type == "heading_open"]
        assert "h1" not in headings, f"{path} repeats its title as a heading"


@pytest.mark.parametrize("locale", ["en", "zh-CN"])
def test_every_page_has_exactly_one_navigation_entry(locale: str) -> None:
    listed = _navigation(DOCS, locale)
    assert len(listed) == len(set(listed)), [path for path in listed if listed.count(path) > 1]
    home = DOCS / ("index.zh-CN.mdx" if locale == "zh-CN" else "index.mdx")
    assert set(listed) == set(_pages(locale)) - {home}


def test_harness_ui_documentation_stays_plain_markdown() -> None:
    # The pages are bundled verbatim into the harness-ui-configuration Skill.
    assert not list((DOCS / "a13n-harness-ui").rglob("*.mdx"))


def test_envd_client_reference_covers_every_generated_method() -> None:
    text = (ROOT / "docs/a13n-envd/python-client.md").read_text(encoding="utf-8")
    documented = set(re.findall(r"^\|\s+`([a-z_]+(?:\.[a-z_]+)?)`\s+\|\s+`[a-z_]+`\s+\|\s+`", text, re.MULTILINE))
    assert documented == set(METHODS)


def test_ui_command_reference_covers_registered_commands_and_options() -> None:
    text = (ROOT / "docs/a13n-harness-ui/command-reference.md").read_text(encoding="utf-8")
    sections = dict(re.findall(r"^### `([^`]+)`\n(.*?)(?=^### |^## |\Z)", text, re.MULTILINE | re.DOTALL))

    def visit(group: click.Group, prefix: str = "") -> None:
        for name, command in group.commands.items():
            path = f"{prefix} {name}".strip()
            if isinstance(command, click.Group):
                visit(command, path)
                continue
            assert path in sections, path
            for parameter in command.params:
                if isinstance(parameter, click.Option) and not parameter.hidden:
                    for option in (*parameter.opts, *parameter.secondary_opts):
                        assert option in sections[path], (path, option)

    visit(cli)
    global_options = text.split("### Global options", 1)[1].split("## Commands", 1)[0]
    for parameter in cli.params:
        if isinstance(parameter, click.Option) and not parameter.hidden:
            for option in (*parameter.opts, *parameter.secondary_opts):
                assert option in global_options, option
    for command in COMMANDS:
        assert f"`{command.usage.replace('|', r'\|')}`" in text, command.name
        for alias in command.aliases:
            assert f"`/{alias}`" in text


def test_ui_http_input_examples_match_current_schemas() -> None:
    from a13n_harness_ui.webui import CreateThreadRequest, RootSteerRequest, SubmitRequest

    text = (ROOT / "docs/a13n-harness-ui/http-api.md").read_text(encoding="utf-8")
    quickstart = text.split("## Create a Thread and submit input", 1)[1].split("## Memory observation", 1)[0]
    bodies = re.findall(r"--data '([^']+)'", quickstart)
    assert len(bodies) == 2
    CreateThreadRequest.model_validate_json(bodies[0])
    SubmitRequest.model_validate_json(bodies[1])
    for heading in ("## Run-only execution environment", "### Ordered input bodies"):
        section = text.split(heading, 1)[1]
        match = re.search(r"^```json\n(.*?)^```", section, re.MULTILINE | re.DOTALL)
        assert match is not None, heading
        SubmitRequest.model_validate_json(match[1])
        if heading == "### Ordered input bodies":
            RootSteerRequest.model_validate_json(match[1])


def test_content_plugin_authoring_examples_match_current_schemas() -> None:
    text = (ROOT / "docs/a13n-harness-ui/skills-and-content-plugins.md").read_text(encoding="utf-8")
    checked = 0
    for block in re.findall(r"^```yaml\n(.*?)^```", text, re.MULTILINE | re.DOTALL):
        value = yaml.safe_load(block)
        if not isinstance(value, dict) or "schema_version" not in value:
            continue
        if "plugins" in value:
            ContentPluginMarketplace.model_validate(value)
        else:
            ContentPluginManifest.model_validate(value)
        checked += 1
    assert checked == 2


def test_service_generated_references_match_current_definitions() -> None:
    from a13n_service.settings import Settings

    namespace = runpy.run_path(str(ROOT / "scripts/docs/references.py"))

    def normalized(text: str) -> str:
        # mdformat pads tables and escapes literal emphasis markers in schema patterns.
        text = re.sub(r"^\|[- :|]+\|\n", "", text, flags=re.MULTILINE)
        text = re.sub(r"\\([*_])", r"\1", text)
        return re.sub(r"\s+", " ", text).strip()

    service_reference = (ROOT / "docs/a13n-service/configuration-reference.md").read_text(encoding="utf-8")
    assert normalized(service_reference) == normalized(namespace["render_configuration"]())

    environment_reference = (ROOT / "docs/environments/configuration-reference.md").read_text(encoding="utf-8")
    assert normalized(environment_reference) == normalized(namespace["render_environment_configuration"]())
    for model in (
        "E2BEnvironmentConfiguration",
        "DaytonaEnvironmentConfiguration",
        "ModalEnvironmentConfiguration",
        "VercelEnvironmentConfiguration",
        "SpritesEnvironmentConfiguration",
        "RunloopEnvironmentConfiguration",
    ):
        assert f"## `{model}`" in environment_reference

    schema = json.loads((ROOT / "scripts/docs/service-settings.schema.json").read_text(encoding="utf-8"))
    assert schema == Settings.model_json_schema()


def test_service_configuration_examples_match_settings() -> None:
    import tomllib

    from a13n_service.settings import Settings

    text = (ROOT / "docs/a13n-service/configuration.md").read_text()
    blocks = re.findall(r"^```toml\n(.*?)^```", text, re.MULTILINE | re.DOTALL)
    assert blocks
    for block in blocks:
        Settings.model_validate(tomllib.loads(block))


def test_logging_reference_covers_every_public_export() -> None:
    text = (ROOT / "docs/a13n-logging/index.md").read_text(encoding="utf-8")
    for name in a13n_logging.__all__:
        assert f"`{name}" in text, name


def test_chinese_documentation_covers_canonical_pages() -> None:
    translated = {Path(str(path).replace(".zh-CN.", ".")) for path in _pages("zh-CN")}
    assert translated == set(_pages("en"))
