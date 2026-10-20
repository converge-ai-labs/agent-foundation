"""Definition-selected request and message-history Filter Capabilities."""
# `_EXPORTS` owns the surface; these imports serve type checkers.

from typing import TYPE_CHECKING, Any

from a13n_harness._exports import load_export

if TYPE_CHECKING:
    from .cold_start import ColdStartFilterCapability, ColdStartFilterConfiguration
    from .content import ContentFilterCapability, ContentFilterConfiguration, MediaFamily
    from .image import ImageFilterCapability
    from .integrity import MessageIntegrityFilterCapability

_EXPORTS = {
    "a13n_harness.filters.cold_start": ("ColdStartFilterCapability", "ColdStartFilterConfiguration"),
    "a13n_harness.filters.content": ("ContentFilterCapability", "ContentFilterConfiguration", "MediaFamily"),
    "a13n_harness.filters.image": ("ImageFilterCapability",),
    "a13n_harness.filters.integrity": ("MessageIntegrityFilterCapability",),
}

__all__ = [
    "ColdStartFilterCapability",
    "ColdStartFilterConfiguration",
    "ContentFilterCapability",
    "ContentFilterConfiguration",
    "ImageFilterCapability",
    "MediaFamily",
    "MessageIntegrityFilterCapability",
]


def __getattr__(name: str) -> Any:
    return load_export(__name__, globals(), _EXPORTS, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
