"""Docker lifecycle configuration owned by Service."""

from typing import Literal, Self
from urllib.parse import urlsplit

from a13n_harness.providers.environment.docker.configuration import DockerEnvironmentConfiguration
from pydantic import Field, model_validator


class DockerRecipe(DockerEnvironmentConfiguration):
    desktop_assistance: bool = Field(default=False, title="Agent-requested desktop assistance")
    desktop_viewer_url: str | None = Field(
        default=None,
        max_length=2048,
        description="Browser-reachable WSS route to container port 6081. Must contain {container_id}; an operator supplies TLS routing.",
    )
    pull_policy: Literal["never", "if_missing"] = "if_missing"

    @model_validator(mode="after")
    def assistance_route(self) -> Self:
        if self.desktop_assistance:
            if not self.desktop:
                raise ValueError("Desktop assistance requires desktop=True")
            route = self.desktop_viewer_url or ""
            parsed = urlsplit(route.replace("{container_id}", "container"))
            if (
                "{container_id}" not in route
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
                or (
                    parsed.scheme != "wss"
                    and not (parsed.scheme == "ws" and parsed.hostname in {"localhost", "127.0.0.1", "::1"})
                )
            ):
                raise ValueError(
                    "Desktop assistance requires a WSS route containing {container_id}; WS is allowed only on loopback"
                )
        return self
