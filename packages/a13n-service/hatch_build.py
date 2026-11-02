import runpy
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    """Package the documentation index and require Console assets for release builds."""

    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict[str, object]) -> None:
        builder = runpy.run_path(str(Path(self.root) / "build_docs.py"))
        bundle = builder["prepare_docs"](Path(self.root))
        # An editable wheel must own this generated resource too: a cached wheel
        # can be installed into a fresh filesystem without running this hook.
        if version == "editable":
            build_data["force_include"] = {str(bundle): "a13n_service/documentation.json"}
        if version != "editable" and not (Path(self.root) / "a13n_service" / "static" / "index.html").is_file():
            raise RuntimeError(
                "The Console build is missing; run `make a13n-service-assets` before building a13n-service"
            )
