"""Versioned at-rest codecs for Run objects, never for live display deltas.

The prefix identifies the object kind independently of its JSON schema. Limits
apply before storage and before decompression; native HarnessState is unchanged.
Call these CPU-bound functions off the service event loop.
"""

from dataclasses import dataclass
from typing import Literal

import zstandard

from a13n_service.infra.errors import ServiceError

type ObjectKind = Literal["state", "display"]
CONTENT_TYPE = "application/zstd"


@dataclass(frozen=True, slots=True)
class ObjectCodec:
    prefix: bytes
    decoded_bytes: int
    encoded_bytes: int

    def encode(self, data: bytes) -> bytes:
        if len(data) > self.decoded_bytes:
            raise ServiceError(
                "payload_too_large", "Run object exceeds its decoded byte limit", {"limit": self.decoded_bytes}
            )
        encoded = self.prefix + zstandard.ZstdCompressor(level=1, write_checksum=True).compress(data)
        if len(encoded) > self.encoded_bytes:
            raise ServiceError(
                "payload_too_large", "Run object exceeds its encoded byte limit", {"limit": self.encoded_bytes}
            )
        return encoded

    def decode(self, data: bytes) -> bytes:
        try:
            if len(data) > self.encoded_bytes or not data.startswith(self.prefix):
                raise ValueError("Invalid Run object envelope")
            frame = data[len(self.prefix) :]
            parameters = zstandard.get_frame_parameters(frame)
            if not parameters.has_checksum:
                raise ValueError("Run object checksum is required")
            if (
                parameters.content_size not in {zstandard.CONTENTSIZE_UNKNOWN, zstandard.CONTENTSIZE_ERROR}
                and parameters.content_size > self.decoded_bytes
            ):
                raise ValueError("Run object exceeds its decoded byte limit")
            decoded = zstandard.ZstdDecompressor().decompress(
                frame, max_output_size=self.decoded_bytes, allow_extra_data=False
            )
            if len(decoded) > self.decoded_bytes:
                raise ValueError("Run object exceeds its decoded byte limit")
            return decoded
        except (ValueError, zstandard.ZstdError) as error:
            raise ServiceError("unavailable", "Run object envelope is invalid", {"dependency": "objects"}) from error


# Display's envelope allows scope/continuity metadata above the maximum 64 MiB
# block-content budget. Neither budget is shared with the resumable state object.
CODECS: dict[ObjectKind, ObjectCodec] = {
    "state": ObjectCodec(b"a13n-state-zstd/1\n", decoded_bytes=256 << 20, encoded_bytes=257 << 20),
    "display": ObjectCodec(b"a13n-display-zstd/1\n", decoded_bytes=72 << 20, encoded_bytes=73 << 20),
}
