"""Documentation build, local-link, and shared-theme regression checks."""

from __future__ import annotations

import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest

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
    assert (assets / "lucide-LICENSE").read_bytes() == (ROOT / "scripts/docs/lucide-LICENSE").read_bytes()
    for icon in (ROOT / "scripts/docs/theme/.icons/lucide").glob("*.svg"):
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
    assert '--md-clipboard-icon: url("data:image/svg+xml,' in css
    warning = (built_site / "a13n-harness-ui/index.html").read_text(encoding="utf-8")
    assert "Choose permissions deliberately." in warning
    assert "!!! warning" not in warning
