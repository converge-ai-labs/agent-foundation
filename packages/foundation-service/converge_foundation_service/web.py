"""Static browser application hosting with bounded history fallback."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from fastapi import FastAPI
from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

_NO_FALLBACK_ROOTS = frozenset({"api", "assets", "healthz", "readyz"})


def _is_browser_route(path: str) -> bool:
    normalized_path = path.strip("/")
    root_segment = normalized_path.partition("/")[0]
    return root_segment not in _NO_FALLBACK_ROOTS and not PurePosixPath(normalized_path).suffix


class SPAStaticFiles(StaticFiles):
    """Serve immutable assets and fall back to the application shell for browser routes."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            response = await super().get_response(path, scope)
        except HTTPException as error:
            if error.status_code != 404 or not _is_browser_route(path):
                raise
        else:
            if response.status_code != 404 or not _is_browser_route(path):
                return response

        return await super().get_response("index.html", scope)


def mount_web_application(app: FastAPI, directory: Path) -> None:
    """Mount one validated production build after all service routes."""

    index_path = directory / "index.html"
    if not index_path.is_file():
        raise ValueError(f"Foundation Web index is missing: {index_path}")
    app.mount("/", SPAStaticFiles(directory=directory, html=True, check_dir=True), name="foundation-web")
