"""Owner images, such as avatars and icons: untrusted raster bytes, stored and served so they stay images.

Only PNG, JPEG and WebP are accepted, recognized by their signature bytes whatever the request declares; SVG and
every other format are refused. An image is stored under a new key below its owner's prefix, and the owner's row
keeps the resulting `ObjectRef` in its `image` column. Replacing or removing an image changes only that reference;
superseded bytes stay stored, since v1 reclaims no objects. Reads verify the bytes against the reference and serve
them with the recognized type, `nosniff` and a CSP that allows nothing, so crafted content never runs as a
document.
"""

import hashlib

from fastapi import Response
from pydantic import JsonValue, TypeAdapter

from a13n_service.infra.errors import invalid, not_found
from a13n_service.infra.http import STORED_CONTENT_HEADERS
from a13n_service.infra.objects.interface import ObjectRef, ObjectStore, new_key, read

TYPES = ("image/png", "image/jpeg", "image/webp")
_BINARY = {"schema": {"type": "string", "format": "binary"}}
# OpenAPI for a route taking an image as its raw request body, and for a route serving one.
UPLOAD = {"requestBody": {"required": True, "content": dict.fromkeys(TYPES, _BINARY)}}
CONTENT: dict[int | str, dict[str, object]] = {
    200: {"description": "The image", "content": dict.fromkeys(TYPES, _BINARY)}
}
_REFERENCE = TypeAdapter(ObjectRef)


def prefix_for(owner_id: str, *, organization_id: str | None) -> str:
    """Where an owner's images live: `orgs/{org}/images/{owner}` for an organization, workspace or agent, and
    `users/{user}/images` for a user, who belongs to no single organization."""
    if organization_id is None:
        return f"users/{owner_id}/images"
    return f"orgs/{organization_id}/images/{owner_id}"


def _image_type(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    raise invalid("image", "unsupported_type")


async def store(
    objects: ObjectStore, prefix: str, data: bytes, *, current: dict[str, JsonValue] | None
) -> dict[str, JsonValue]:
    """Store a PNG, JPEG or WebP image under `prefix`; the result is what its owner's `image` column holds.

    The owner's `current` image is kept when it already holds these bytes, so setting the same image again
    changes nothing.
    """
    content_type = _image_type(data)
    if current is not None and _REFERENCE.validate_python(current).digest == hashlib.sha256(data).hexdigest():
        return current
    reference = await objects.put(new_key(prefix), data, content_type=content_type)
    return _REFERENCE.dump_python(reference, mode="json")


def url(path: str, image: dict[str, JsonValue] | None) -> str | None:
    """The owner's image route, carrying the digest so the URL changes whenever the image does."""
    return None if image is None else f"{path}?v={_REFERENCE.validate_python(image).digest}"


async def serve(objects: ObjectStore, owner_id: str, image: dict[str, JsonValue] | None) -> Response:
    """The owner's current image as a response; an owner without one has no image to find."""
    if image is None:
        raise not_found("image", owner_id)
    reference = _REFERENCE.validate_python(image)
    return Response(
        await read(objects, reference),
        media_type=reference.content_type,
        headers=STORED_CONTENT_HEADERS,
    )
