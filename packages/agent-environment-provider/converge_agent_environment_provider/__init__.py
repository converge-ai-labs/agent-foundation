"""Shared environment-provider boundary for Converge agents."""

from importlib.metadata import version

__version__ = version("converge-agent-environment-provider")

__all__ = ["__version__"]
