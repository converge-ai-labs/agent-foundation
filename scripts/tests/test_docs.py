"""Documentation build, local-link, and shared-theme regression checks."""

from __future__ import annotations

import json
import re
import runpy
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

import a13n_logging
import click
import pytest
import yaml
from a13n_envd_client.eip.v1 import METHODS
from a13n_harness_ui.cli import cli
from a13n_harness_ui.content_plugins import ContentPluginManifest, ContentPluginMarketplace
from a13n_harness_ui.interactive.commands import COMMANDS

ROOT = Path(__file__).resolve().parents[2]


class PageLinks(HTMLParser):
    def __init__(self, text: str) -> None:
        super().__init__()
        self.ids: set[str] = set()
        self.links: list[str] = []
        self.feed(text)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if identifier := values.get("id"):
            self.ids.add(identifier)
        if tag == "a" and (href := values.get("href")):
            self.links.append(href)


@pytest.fixture(scope="module")
def built_site(tmp_path_factory: pytest.TempPathFactory) -> Path:
    site = tmp_path_factory.mktemp("docs-site")
    subprocess.run(
        [sys.executable, "-m", "mkdocs", "build", "--strict", "--site-dir", str(site)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return site


def test_generated_pages_have_valid_local_links_and_anchors(built_site: Path) -> None:
    pages = {path.resolve(): PageLinks(path.read_text(encoding="utf-8")) for path in built_site.rglob("*.html")}
    errors: list[str] = []
    for source, page in pages.items():
        for link in page.links:
            url = urlsplit(link)
            if url.scheme or url.netloc:
                continue
            target = (source.parent / unquote(url.path)).resolve() if url.path else source
            if url.path.startswith("/"):
                target = (built_site / unquote(url.path).lstrip("/")).resolve()
            if target.is_dir():
                target /= "index.html"
            if not target.exists():
                errors.append(f"{source.relative_to(built_site)} -> {link}: missing file")
            elif url.fragment and target in pages and unquote(url.fragment) not in pages[target].ids:
                errors.append(f"{source.relative_to(built_site)} -> {link}: missing anchor")
    assert not errors, "\n".join(errors)


def test_theme_reuses_frontend_brand_and_bundles_license(built_site: Path) -> None:
    assets = built_site / "assets/a13n"
    ui = ROOT / "frontend/packages/a13n-ui/src"
    for generated, source in {
        "logo.svg": "brand/a13n-logo.svg",
        "SpaceGrotesk-Bold.woff2": "brand/SpaceGrotesk-Bold.woff2",
        "SpaceGrotesk-OFL.txt": "brand/SpaceGrotesk-OFL.txt",
        "tokens.css": "styles/tokens.css",
    }.items():
        assert (assets / generated).read_bytes() == (ui / source).read_bytes()
    assert (assets / "phosphor-LICENSE").read_bytes() == (ROOT / "scripts/docs/phosphor-LICENSE").read_bytes()
    for icon in (ROOT / "scripts/docs/theme/.icons/phosphor").glob("*.svg"):
        assert (assets / "icons" / icon.name).read_bytes() == icon.read_bytes()


def test_overview_has_mermaid_markup_and_local_theme_assets(built_site: Path) -> None:
    html = (built_site / "index.html").read_text(encoding="utf-8")
    assert 'class="mermaid"' in html
    assert "language-mermaid" not in html
    assert "assets/a13n/theme.css" in html
    assert "a13n-wordmark" in html
    assert 'data-md-color-scheme="default"' in html
    assert "Switch to dark mode" in html
    assert "fonts.googleapis.com" not in html
    assert "fonts.gstatic.com" not in html
    css = (built_site / "assets/a13n/theme.css").read_text(encoding="utf-8")
    assert 'url("icons/' not in css
    assert '--md-code-copy-icon: url("data:image/svg+xml,' in css
    warning = (built_site / "a13n-harness-ui/index.html").read_text(encoding="utf-8")
    assert "Choose permissions deliberately." in warning
    assert "!!! warning" not in warning


def test_mermaid_fences_disable_shrink_to_fit(built_site: Path) -> None:
    diagrams = [
        source
        for page in built_site.rglob("*.html")
        for source in re.findall(r'<pre class="mermaid"><code>(.*?)</code></pre>', page.read_text(), re.DOTALL)
    ]
    assert diagrams
    for source in diagrams:
        _, configuration, graph = source.split("---", 2)
        settings = yaml.safe_load(configuration)["config"]
        assert settings["flowchart"]["useMaxWidth"] is False
        assert settings["sequence"]["useMaxWidth"] is False
        assert graph.strip().startswith(("flowchart", "sequenceDiagram"))


def test_envd_client_reference_covers_every_generated_method() -> None:
    text = (ROOT / "docs/a13n-envd/python-client.md").read_text(encoding="utf-8")
    documented = set(re.findall(r"^\|\s+`([a-z_]+(?:\.[a-z_]+)?)`\s+\|\s+`[a-z_]+`\s+\|\s+`", text, re.MULTILINE))
    assert documented == set(METHODS)


def test_every_document_has_exactly_one_navigation_entry() -> None:
    configuration = yaml.safe_load((ROOT / "mkdocs.yml").read_text(encoding="utf-8"))
    documented: list[str] = []

    def visit(items: list[dict[str, object]]) -> None:
        for item in items:
            for value in item.values():
                if isinstance(value, list):
                    visit(value)
                else:
                    assert isinstance(value, str)
                    documented.append(value)

    visit(configuration["nav"])
    expected = {str(path.relative_to(ROOT / "docs")) for path in (ROOT / "docs").rglob("*.md")}
    assert set(documented) == expected
    assert len(documented) == len(set(documented))


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


def test_service_generated_references_match_current_definitions(built_site: Path) -> None:
    from a13n_service.settings import Settings

    namespace = runpy.run_path(str(ROOT / "scripts/docs/references.py"))

    def normalized(text: str) -> str:
        # mdformat pads tables and escapes literal emphasis markers in schema patterns.
        text = re.sub(r"^\|[- :|]+\|\n", "", text, flags=re.MULTILINE)
        text = re.sub(r"\\([*_])", r"\1", text)
        return re.sub(r"\s+", " ", text).strip()

    for filename, renderer in (
        ("configuration-reference.md", "render_configuration"),
        ("api-reference.md", "render_native_api"),
    ):
        actual = (ROOT / "docs/a13n-service" / filename).read_text(encoding="utf-8")
        assert normalized(actual) == normalized(namespace[renderer]()), filename

    schema = json.loads((ROOT / "scripts/docs/service-settings.schema.json").read_text(encoding="utf-8"))
    assert schema == Settings.model_json_schema()
    for generated, source in {
        "service-openapi.json": ROOT / "sdk/typescript/openapi.json",
        "service-settings.json": ROOT / "scripts/docs/service-settings.schema.json",
    }.items():
        assert (built_site / "assets/reference" / generated).read_bytes() == source.read_bytes()


def test_service_workflow_examples_match_request_models() -> None:
    from a13n_service.agents.domain import AgentRunOverride, CreateAgentRequest
    from a13n_service.connectivity.connections.domain import CreateConnectionRequest
    from a13n_service.gateway.requests import StartRunRequest
    from a13n_service.memory.domain import CreateMemoryProviderRequest
    from a13n_service.skills.domain import CreateSkillRequest

    def blocks(name: str) -> list[dict[str, object]]:
        text = (ROOT / "docs/a13n-service" / name).read_text(encoding="utf-8")
        return [json.loads(block) for block in re.findall(r"^```json\n(.*?)^```", text, re.MULTILINE | re.DOTALL)]

    agent, permissions, run = blocks("agents-and-runs.md")
    CreateAgentRequest.model_validate(agent)
    reviewer = permissions["reviewer"]
    assert isinstance(reviewer, dict)
    assert reviewer["model"] == "REVIEW_MODEL_ID"
    # The guide explicitly requires replacing this placeholder with a managed Model ID.
    reviewer["model"] = "mdl_0123456789abcdef0123"
    AgentRunOverride.model_validate(permissions)
    StartRunRequest.model_validate(run)
    for connection in blocks("external-tools.md"):
        if "source" in connection:
            CreateConnectionRequest.model_validate(connection)
    skill = next(value for value in blocks("resources.md") if "source" in value)
    CreateSkillRequest.model_validate(skill)
    provider, memory = blocks("memory.md")
    CreateMemoryProviderRequest.model_validate(provider)
    AgentRunOverride.model_validate(memory)


def test_logging_reference_covers_every_public_export() -> None:
    text = (ROOT / "docs/a13n-logging/index.md").read_text(encoding="utf-8")
    for name in a13n_logging.__all__:
        assert f"`{name}" in text, name
