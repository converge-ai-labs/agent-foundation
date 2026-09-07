"""Object storage contract and adapters."""

from .api import (
    ByteRange,
    InvalidObjectRequest,
    ObjectAccessDenied,
    ObjectConflict,
    ObjectInfo,
    ObjectNotFound,
    ObjectPage,
    ObjectReader,
    ObjectSource,
    ObjectStore,
    ObjectStoreError,
    ObjectStoreUnavailable,
    ObjectSummary,
)
from .local import LocalObjectStore
from .s3 import S3ObjectStore

__all__ = [
    "ByteRange",
    "InvalidObjectRequest",
    "LocalObjectStore",
    "ObjectAccessDenied",
    "ObjectConflict",
    "ObjectInfo",
    "ObjectNotFound",
    "ObjectPage",
    "ObjectReader",
    "ObjectSource",
    "ObjectStore",
    "ObjectStoreError",
    "ObjectStoreUnavailable",
    "ObjectSummary",
    "S3ObjectStore",
]
