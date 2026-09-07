"""Internal a13n Service components."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("a13n-service")
except PackageNotFoundError:
    __version__ = "0.0.0"

__all__ = ["__version__"]
