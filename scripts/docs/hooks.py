"""Expose shared frontend brand assets without adding a Node.js docs build step."""

from pathlib import Path
from urllib.parse import quote

from markdown import Markdown
from mkdocs.config.defaults import MkDocsConfig
from mkdocs.structure.files import File, Files
from pymdownx.superfences import fence_code_format

ROOT = Path(__file__).resolve().parents[2]
UI = ROOT / "frontend/packages/a13n-ui/src"
THEME = Path(__file__).resolve().parent


def _format_mermaid(
    source: str,
    language: str,
    class_name: str,
    options: dict[str, object],
    md: Markdown,
    **kwargs: object,
) -> str:
    # Material renders into a closed shadow root. Size the SVG through Mermaid,
    # not an unreachable CSS selector, so the outer region can scroll naturally.
    configuration = "---\nconfig:\n  flowchart:\n    useMaxWidth: false\n  sequence:\n    useMaxWidth: false\n---\n"
    return fence_code_format(configuration + source, language, class_name, options, md, **kwargs)


def on_config(config: MkDocsConfig) -> MkDocsConfig:
    """Keep diagram labels readable instead of shrinking wide graphs to fit."""
    for fence in config.mdx_configs["pymdownx.superfences"]["custom_fences"]:
        if fence["name"] == "mermaid":
            fence["format"] = _format_mermaid
    return config


def on_files(files: Files, config: MkDocsConfig) -> Files:
    """Keep Markdown content separate from site assets and their source owners."""
    assets = {
        "assets/a13n/tokens.css": UI / "styles/tokens.css",
        "assets/a13n/logo.svg": UI / "brand/a13n-logo.svg",
        "assets/a13n/SpaceGrotesk-Bold.woff2": UI / "brand/SpaceGrotesk-Bold.woff2",
        "assets/a13n/SpaceGrotesk-OFL.txt": UI / "brand/SpaceGrotesk-OFL.txt",
        "assets/a13n/phosphor-LICENSE": THEME / "phosphor-LICENSE",
        "assets/a13n/LICENSE.coss": UI.parent / "LICENSE.coss",
        "assets/reference/service-openapi.json": ROOT / "proto/a13n-service/openapi.json",
        "assets/reference/harness-ui-openapi.json": ROOT / "frontend/apps/a13n-harness-ui/src/openapi.json",
        "assets/reference/service-settings.json": THEME / "service-settings.schema.json",
    }
    license_text = (UI.parent / "LICENSE.coss").read_text(encoding="utf-8")
    stylesheet = f"/*!\n{license_text.rstrip()}\n*/\n" + (THEME / "theme.css").read_text(encoding="utf-8")
    for icon in sorted((THEME / "theme/.icons/phosphor").glob("*.svg")):
        assets[f"assets/a13n/icons/{icon.name}"] = icon
        # CSS-variable URLs resolve at their use site in Material's stylesheet.
        # Embed mask icons so nested pages and site URL prefixes both work.
        encoded = quote(icon.read_text(encoding="utf-8").strip(), safe="")
        stylesheet = stylesheet.replace(f'url("icons/{icon.name}")', f'url("data:image/svg+xml,{encoded}")')
    files.append(File.generated(config, "assets/a13n/theme.css", content=stylesheet))
    for destination, source in assets.items():
        files.append(File.generated(config, destination, abs_src_path=str(source)))
    return files
