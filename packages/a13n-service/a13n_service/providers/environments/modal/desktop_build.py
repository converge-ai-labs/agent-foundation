"""Pinned public desktop assets built by Modal; credentials never enter image layers."""

import hashlib
import json
import re

from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.providers.environment.modal.shared import ModalConnectionConfiguration, ModalCredential, sdk_errors
from a13n_harness.providers.environment.native.errors import failure
from pydantic import JsonValue

BASE_IMAGE = "debian:trixie-slim@sha256:4876fdea29cac1a33a3195f1eb1952feb1fca7dd6dd00be3e2f1879cf20d34d2"
SOURCE_REVISION = "25bab435d27e3d30fc038bfa8d2f20e5d70cf783"
RELEASE_URL = "https://github.com/converge-ai-labs/agent-foundation/releases/download/release/a13n-envd-v0.5.1/a13n-envd-0.5.1-x86_64-unknown-linux-gnu.tar.gz"
RELEASE_SHA256 = "f2fd6eace212c2e59d2b4d6a2dc69ec010235005ad332ac1ddd1932f7bca258c"
FILES = (
    (
        "deploy/docker/images/sandbox/entrypoint.sh",
        "/usr/local/bin/a13n-sandbox-entrypoint",
        "b3c53fb45406e3206ad6465bf4461b5fba385da67d1f11a3c534a83e066a0496",
    ),
    (
        "deploy/docker/images/sandbox/desktop.py",
        "/usr/local/bin/a13n-sandbox-desktop",
        "455fbe3413af57fe36fea0f4a88a1d5bfbfb6bbc6f044121d46f0ce210e785b9",
    ),
    (
        "deploy/docker/images/sandbox/init-skills.sh",
        "/usr/local/bin/a13n-sandbox-init",
        "e973546b911758e35c629d093d7b0496ce7e5af0342a7a10570f192a43508f47",
    ),
    (
        "packages/a13n-service/a13n_service/providers/environment/desktop_guest.py",
        "/opt/a13n-desktop-access.py",
        "7d31432661c8199a95bed5214088a2d3589872f787ad55b28566ba9b659b9571",
    ),
)


def specification() -> dict[str, JsonValue]:
    install = (
        "apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "
        "curl python3 ca-certificates libgcc-s1 util-linux procps sudo xvfb openbox xauth x11-utils x11-xkb-utils "
        "x11vnc websockify fonts-noto-cjk fonts-noto-color-emoji chromium xterm mousepad xdotool "
        "&& groupadd --gid 1000 sandbox && useradd --uid 1000 --gid sandbox --create-home --shell /bin/bash sandbox "
        "&& install -d -o sandbox -g sandbox /workspace "
        "&& install -d -m 0700 /var/lib/a13n-envd /run/a13n-envd-state "
        "&& rm -rf /var/lib/apt/lists/*"
    )
    commands: list[JsonValue] = [
        install,
        f"curl -fL --retry 2 --max-time 120 '{RELEASE_URL}' -o /tmp/envd.tar.gz && echo '{RELEASE_SHA256}  /tmp/envd.tar.gz' | sha256sum -c - && tar -xOzf /tmp/envd.tar.gz a13n-envd > /usr/local/bin/a13n-envd && chmod 0755 /usr/local/bin/a13n-envd && rm /tmp/envd.tar.gz",
    ]
    for source, destination, digest in FILES:
        url = f"https://raw.githubusercontent.com/converge-ai-labs/agent-foundation/{SOURCE_REVISION}/{source}"
        commands.append(
            f"curl -fL --retry 2 --max-time 60 '{url}' -o '{destination}' && echo '{digest}  {destination}' | sha256sum -c - && chmod 0755 '{destination}'"
        )
    commands.append(
        "test ! -e /var/lib/a13n-envd/http-token && test ! -e /run/a13n-desktop-access/control.sock && /usr/local/bin/a13n-envd --version"
    )
    return {"base": BASE_IMAGE, "commands": commands}


def revision() -> str:
    return hashlib.sha256(json.dumps(specification(), sort_keys=True).encode()).hexdigest()


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
