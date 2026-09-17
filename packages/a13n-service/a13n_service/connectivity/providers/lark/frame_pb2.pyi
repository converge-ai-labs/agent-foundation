from collections.abc import Iterable as _Iterable
from collections.abc import Mapping as _Mapping
from typing import ClassVar as _ClassVar

from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from google.protobuf.internal import containers as _containers

DESCRIPTOR: _descriptor.FileDescriptor

class Header(_message.Message):
    __slots__ = ("key", "value")
    KEY_FIELD_NUMBER: _ClassVar[int]
    VALUE_FIELD_NUMBER: _ClassVar[int]
    key: str
    value: str
    def __init__(self, key: str | None = ..., value: str | None = ...) -> None: ...

class Frame(_message.Message):
    __slots__ = (
        "LogID",
        "LogIDNew",
        "SeqID",
        "headers",
        "method",
        "payload",
        "payload_encoding",
        "payload_type",
        "service",
    )
    SEQID_FIELD_NUMBER: _ClassVar[int]
    LOGID_FIELD_NUMBER: _ClassVar[int]
    SERVICE_FIELD_NUMBER: _ClassVar[int]
    METHOD_FIELD_NUMBER: _ClassVar[int]
    HEADERS_FIELD_NUMBER: _ClassVar[int]
    PAYLOAD_ENCODING_FIELD_NUMBER: _ClassVar[int]
    PAYLOAD_TYPE_FIELD_NUMBER: _ClassVar[int]
    PAYLOAD_FIELD_NUMBER: _ClassVar[int]
    LOGIDNEW_FIELD_NUMBER: _ClassVar[int]
    SeqID: int
    LogID: int
    service: int
    method: int
    headers: _containers.RepeatedCompositeFieldContainer[Header]
    payload_encoding: str
    payload_type: str
    payload: bytes
    LogIDNew: str
    def __init__(
        self,
        SeqID: int | None = ...,
        LogID: int | None = ...,
        service: int | None = ...,
        method: int | None = ...,
        headers: _Iterable[Header | _Mapping] | None = ...,
        payload_encoding: str | None = ...,
        payload_type: str | None = ...,
        payload: bytes | None = ...,
        LogIDNew: str | None = ...,
    ) -> None: ...
