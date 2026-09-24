"""Memory Provider definitions shipped with the Harness."""

from .definition import MemoryProviderDefinition
from .mem0 import MEM0_OSS, MEM0_PLATFORM

BUILT_IN_MEMORY_PROVIDERS: tuple[MemoryProviderDefinition, ...] = (MEM0_PLATFORM, MEM0_OSS)
