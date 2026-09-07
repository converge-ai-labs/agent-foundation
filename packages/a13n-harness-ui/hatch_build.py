from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    """Require prepared Harness UI WebUI assets in every Harness UI artifact."""

    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict[str, object]) -> None:
        del build_data
        if version == "editable":
            return
        static_path = Path(self.root) / "a13n_harness_ui" / "static"
        required = (static_path / "index.html", static_path / "asset-manifest.json")
        if not all(path.is_file() for path in required):
            raise RuntimeError(
                "Harness UI WebUI assets are missing; run `make a13n-harness-ui-assets` before building a13n-harness-ui"
            )
