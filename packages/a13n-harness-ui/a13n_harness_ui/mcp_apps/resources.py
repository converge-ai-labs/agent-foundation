"""Same-server resource references, not Host URLs or file paths."""

from fastmcp.resources.template import expand_uri_template, match_uri_template
from pydantic import Field, JsonValue

from .models import AppModel


class AppResourceRequest(AppModel):
    uri: str = Field(min_length=1, max_length=2048)


def matches_template(uri: str, template: str) -> bool:
    # Upstream's matcher alone ignores extra query parameters. Require its exact
    # inverse too: unsupported or non-round-tripping expressions fail closed.
    parameters = match_uri_template(uri, template)
    return parameters is not None and expand_uri_template(template, parameters) == uri


def result_resource_uris(result: dict[str, JsonValue]) -> set[str]:
    """Only protocol resource links/embedded resources confer a returned reference."""
    content = result.get("content")
    if not isinstance(content, list):
        return set()
    uris: set[str] = set()
    for item in content:
        if not isinstance(item, dict):
            continue
        resource = item.get("resource") if item.get("type") == "resource" else item
        if item.get("type") not in {"resource", "resource_link"} or not isinstance(resource, dict):
            continue
        uri = resource.get("uri")
        if isinstance(uri, str) and 0 < len(uri) <= 2048:
            uris.add(uri)
    return uris
