"""Shared profile image normalization and bounded object I/O."""

import warnings
from io import BytesIO

from anyio import to_thread
from PIL import Image, ImageOps, UnidentifiedImageError

from a13n_service.application_errors import ApplicationError, ErrorCategory
from a13n_service.storage import ObjectStore

MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_000_000


def normalize_image(content: bytes) -> bytes:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(content)) as image:
                if image.format not in {"JPEG", "PNG", "WEBP"} or image.width * image.height > MAX_IMAGE_PIXELS:
                    raise ValueError("Unsupported image")
                image.load()
                image = ImageOps.exif_transpose(image).convert("RGBA")
                image.thumbnail((512, 512))
                output = BytesIO()
                image.save(output, format="WEBP", quality=90)
                return output.getvalue()
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as error:
        raise ApplicationError(
            "invalid_profile_image",
            "Use a PNG, JPEG or WebP image up to 16 megapixels.",
            category=ErrorCategory.invalid_request,
        ) from error


async def write_image(objects: ObjectStore, key: str, content: bytes) -> None:
    if not content or len(content) > MAX_IMAGE_BYTES:
        raise ApplicationError(
            "invalid_profile_image",
            "Images must contain between 1 byte and 5 MiB.",
            category=ErrorCategory.invalid_request,
        )
    normalized = await to_thread.run_sync(normalize_image, content)
    await objects.put(key, normalized, content_type="image/webp", if_none_match=True)


async def read_image(objects: ObjectStore, key: str) -> bytes:
    async with objects.open(key) as reader:
        data = bytearray()
        async for chunk in reader:
            data.extend(chunk)
            if len(data) > MAX_IMAGE_BYTES:
                raise ApplicationError(
                    "profile_image_unavailable", "The image is unavailable.", category=ErrorCategory.unavailable
                )
        return bytes(data)
