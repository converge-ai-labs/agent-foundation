"""The multipart upload request and the handle the API returns."""

from typing import Annotated

from fastapi import UploadFile
from pydantic import BaseModel, StringConstraints

UploadId = Annotated[str, StringConstraints(pattern=r"^upl_[a-f0-9]{32}$")]
Digest = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
FileName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255, pattern=r"^[^\x00-\x1f\x7f]+$")
]
ContentType = Annotated[
    str,
    StringConstraints(
        to_lower=True, min_length=3, max_length=127, pattern=r"^[a-zA-Z0-9!#$&^_.+-]+/[a-zA-Z0-9!#$&^_.+-]+$"
    ),
]


class UploadCreate(BaseModel):
    """The multipart form: one file part named `file`."""

    file: UploadFile


class Upload(BaseModel):
    upload_id: str
    filename: str
    content_type: str
    size: int
    digest: str
