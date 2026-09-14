"""Browser authorization URL policy shared by configuration and handoff."""

from urllib.parse import SplitResult, urlsplit

_HTTP_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def split_browser_url(value: str) -> SplitResult:
    """Accept HTTPS everywhere and HTTP only on an exact loopback host."""

    target = urlsplit(value)
    try:
        _ = target.port
    except ValueError as error:
        raise ValueError("Browser URL has an invalid port") from error
    if (
        not target.hostname
        or target.username is not None
        or target.password is not None
        or target.fragment
        or not (target.scheme == "https" or (target.scheme == "http" and target.hostname in _HTTP_LOOPBACK_HOSTS))
    ):
        raise ValueError("Browser URL must use HTTPS or exact loopback HTTP")
    return target


def is_secure_or_loopback_url(value: str) -> bool:
    try:
        split_browser_url(value)
    except ValueError:
        return False
    return True
