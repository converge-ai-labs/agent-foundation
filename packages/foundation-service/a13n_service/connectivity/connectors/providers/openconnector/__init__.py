"""OOMOL managed Provider and separate personal/self-hosted runtime."""

from .project import OpenConnectorProvider
from .runtime import OpenConnectorRuntime

__all__ = ["OpenConnectorProvider", "OpenConnectorRuntime"]
