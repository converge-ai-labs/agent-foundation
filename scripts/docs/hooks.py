"""Expose shared frontend brand assets without adding a Node.js docs build step."""

from pathlib import Path
from urllib.parse import quote

from mkdocs.config.defaults import MkDocsConfig
from mkdocs.structure.files import File, Files

ROOT = Path(__file__).resolve().parents[2]
UI = ROOT / "frontend/packages/a13n-ui/src"
THEME = Path(__file__).resolve().parent


def on_files(files: Files, config: MkDocsConfig) -> Files:
    """Keep Markdown content separate from site assets and their source owners."""
    assets = {
        "assets/a13n/tokens.css": UI / "styles/tokens.css",
        "assets/a13n/logo.svg": UI / "brand/a13n-logo.svg",
        "assets/a13n/SpaceGrotesk-Bold.woff2": UI / "brand/SpaceGrotesk-Bold.woff2",
        "assets/a13n/SpaceGrotesk-OFL.txt": UI / "brand/SpaceGrotesk-OFL.txt",
        "assets/a13n/lucide-LICENSE": THEME / "lucide-LICENSE",
    }
    stylesheet = (THEME / "theme.css").read_text(encoding="utf-8")
    for icon in sorted((THEME / "theme/.icons/lucide").glob("*.svg")):
        assets[f"assets/a13n/icons/{icon.name}"] = icon
        # CSS-variable URLs resolve at their use site in Material's stylesheet.
        # Embed mask icons so nested pages and site URL prefixes both work.
        encoded = quote(icon.read_text(encoding="utf-8").strip(), safe="")
        stylesheet = stylesheet.replace(f'url("icons/{icon.name}")', f'url("data:image/svg+xml,{encoded}")')
    files.append(File.generated(config, "assets/a13n/theme.css", content=stylesheet))
    for destination, source in assets.items():
        files.append(File.generated(config, destination, abs_src_path=str(source)))
    return files
