from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    """Require the Console build that the package serves, except in editable development installs."""

    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict[str, object]) -> None:
        del build_data
        if version != "editable" and not (Path(self.root) / "a13n_service" / "static" / "index.html").is_file():
            raise RuntimeError(
                "The Console build is missing; run `make a13n-service-assets` before building a13n-service"
            )
