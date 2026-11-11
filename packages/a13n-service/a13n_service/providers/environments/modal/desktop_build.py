"""Pinned public desktop assets built by Modal; credentials never enter image layers."""

import hashlib
import json
import re

from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.providers.environment.modal.shared import ModalConnectionConfiguration, ModalCredential, sdk_errors
from a13n_harness.providers.environment.native.errors import failure
from pydantic import JsonValue

from a13n_service.providers.environment import envd_assets
from a13n_service.providers.environment.envd_release import installation_command


def specification(profile: envd_assets.Profile = "desktop", base_image: str | None = None) -> dict[str, JsonValue]:
    packages = "curl ca-certificates libgcc-s1 util-linux procps sudo"
    if profile == "desktop":
        packages += (
            " python3 xvfb openbox xauth x11-utils x11-xkb-utils x11vnc websockify"
            " fonts-noto-cjk fonts-noto-color-emoji chromium xterm mousepad xdotool"
        )
    install = (
        "apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "
        f"{packages} "
        "&& groupadd --gid 1000 sandbox && useradd --uid 1000 --gid sandbox --create-home --shell /bin/bash sandbox "
        "&& install -d -o sandbox -g sandbox /workspace "
        "&& install -d -m 0700 /var/lib/a13n-envd /run/a13n-envd-state "
        "&& rm -rf /var/lib/apt/lists/*"
    )
    commands: list[JsonValue] = [
        install,
        installation_command(),
    ]
    if base_image is not None:
        commands.insert(0, envd_assets.base_preflight())
    commands.extend(envd_assets.commands(profile))
    commands.append(
        "test ! -e /var/lib/a13n-envd/http-token && test ! -e /run/a13n-desktop-access/control.sock && /usr/local/bin/a13n-envd --version"
    )
    return {"base": base_image or envd_assets.BASE_IMAGE, "commands": commands}


def revision(profile: envd_assets.Profile = "desktop", base_image: str | None = None) -> str:
    return hashlib.sha256(json.dumps(specification(profile, base_image), sort_keys=True).encode()).hexdigest()


def locator(configuration: ModalConnectionConfiguration) -> str:
    from modal.config import config

    return json.dumps([configuration.model_dump(), config["server_url"]], sort_keys=True)


class DesktopBuildAPI:
    def __init__(self, configuration: ModalConnectionConfiguration, credential: ModalCredential):
        self.configuration = configuration
        self.credential = credential
        self.client = None

    async def build(self, manifest: dict[str, JsonValue], scope: str, image_id: str | None) -> str:
        import modal
        from modal.config import config
        from modal_proto import api_pb2

        # Forced rebuilds bypass Modal's content-addressed get-or-create semantics.
        if config.get("force_build") or config.get("ignore_cache"):
            raise failure("modal", "desktop_build_cache_disabled", Category.INVALID)
        with sdk_errors():
            self.client = modal.Client(
                config["server_url"],
                api_pb2.CLIENT_TYPE_CLIENT,
                (
                    self.credential.token_id.get_secret_value(),
                    self.credential.token_secret.get_secret_value(),
                ),
            )
            await self.client.__aenter__()
            try:
                app = await modal.App.lookup.aio(
                    self.configuration.app_name,
                    environment_name=self.configuration.environment_name,
                    create_if_missing=image_id is None,
                    client=self.client,
                )
                if image_id is not None:
                    # Resolve using current credentials even on a ledger hit; cached IDs confer no authority.
                    image = await modal.Image.from_id.aio(image_id, client=self.client)
                else:
                    base, commands = manifest.get("base"), manifest.get("commands")
                    if (
                        not isinstance(base, str)
                        or not isinstance(commands, list)
                        or not all(isinstance(c, str) for c in commands)
                    ):
                        raise failure("modal", "desktop_build_manifest_invalid", Category.INVALID)
                    image = (
                        modal.Image.from_registry(base)
                        .run_commands(*[command for command in commands if isinstance(command, str)])
                        .workdir("/workspace")
                        .entrypoint([])
                    )
                    # Scope contains no credential material. Isolate final images while sharing public dependency layers.
                    image = image.env({"A13N_DESKTOP_BUILD_SCOPE": scope})
                await image.build.aio(app)
            except modal.exception.ImageBuildError:
                raise failure("modal", "desktop_image_build_failed", Category.CONFLICT) from None
            except modal.exception.NotFoundError:
                raise failure("modal", "desktop_image_missing", Category.MISSING) from None
            result = image.object_id
            if not re.fullmatch(r"im-[A-Za-z0-9]+", result) or (image_id is not None and result != image_id):
                raise failure("modal", "desktop_image_identity_mismatch", Category.CONFLICT)
            return result

    async def close(self) -> None:
        client, self.client = self.client, None
        if client is not None:
            await client.__aexit__(None, None, None)
