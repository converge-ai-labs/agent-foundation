"""Built-in Memory definitions use the same authoring contract as installed providers."""

from .mem0_oss import DEFINITION as MEM0_OSS
from .mem0_platform import DEFINITION as MEM0_PLATFORM

BUILT_IN_MEMORY_PROVIDERS = (MEM0_OSS, MEM0_PLATFORM)
