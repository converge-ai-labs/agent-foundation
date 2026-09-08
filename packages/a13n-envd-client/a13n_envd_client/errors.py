from __future__ import annotations

from a13n_envd_client.eip.v1 import DataResetStatus, EIPError


class EIPClientError(Exception):
    """Base class for low-level EIP client failures."""


class EIPProtocolError(EIPClientError):
    """The peer violated JSON-RPC, EIP, or framing rules."""


class EIPTransportError(EIPClientError):
    """The transport failed before a valid correlated response arrived."""


class EIPTransportClosedError(EIPTransportError):
    """The transport closed while it could still have in-flight requests."""


class EIPConnectionError(EIPTransportError):
    """Connection establishment or exchange failed without an EIP response.

    This does not establish whether a request was dispatched and is not blanket
    permission to retry operations.
    """


class EIPRequestTimeoutError(EIPTransportError):
    """A local transport wait expired without claiming an operation outcome."""

    def __init__(self, message: str, *, dispatched: bool) -> None:
        super().__init__(message)
        self.dispatched = dispatched


class EIPMethodError(EIPClientError):
    """A correlated request completed with a typed EIP error."""

    def __init__(self, error: EIPError) -> None:
        super().__init__(f"EIP method failed ({error.code}): {error.message}")
        self.error = error


class EIPSessionStateError(EIPClientError):
    """The initialized-session API was used in an invalid local state."""


class EIPTransferError(EIPClientError):
    """One typed file transfer reset or failed without becoming carrier-terminal."""

    def __init__(
        self,
        message: str,
        *,
        status: DataResetStatus | None = None,
        offset: int | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.offset = offset
