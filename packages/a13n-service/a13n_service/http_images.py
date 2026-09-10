"""Shared bounded HTTP image upload and private content response."""

from anyio import fail_after
from fastapi import Request, Response

from a13n_service.application_errors import ApplicationError, ErrorCategory
from a13n_service.profile_images import MAX_IMAGE_BYTES

IMAGE_UPLOAD = {
    "requestBody": {
        "required": True,
        "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}},
    }
}


async def image_body(request: Request) -> bytes:
    content = bytearray()
    with fail_after(30):
        async for chunk in request.stream():
            content.extend(chunk)
            if len(content) > MAX_IMAGE_BYTES:
                raise ApplicationError(
                    "invalid_profile_image",
                    "Images must be no larger than 5 MiB.",
                    category=ErrorCategory.invalid_request,
                )
    return bytes(content)


def image_response(content: bytes) -> Response:
    return Response(
        content,
        media_type="image/webp",
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )
