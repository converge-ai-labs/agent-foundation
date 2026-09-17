# Installation icons

These PNG source assets use the shared `frontend/packages/a13n-ui/src/brand/a13n-logo.svg` mark without changing its paths or colors. They have an opaque `#fbfbfd` background for platform masks.

To reproduce them, render the shared SVG's `194 190 690 690` view box inside a 512×512 square with 48 pixels of padding on each edge, then export at 192×192, 512×512, and 180×180 (Apple touch icon). The separate 512×512 maskable icon uses 104 pixels of padding, keeping the complete mark inside the central safe circle. Rasterization used CairoSVG; it is not a build or runtime dependency.

The server exposes only the named PNGs, not this source note. The manifest has a stable root identity and no instance credentials or conversation-specific launch URL.
