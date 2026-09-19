"""Built-in Memory definitions use the same authoring contract as installed providers."""

from .definition import MemoryProviderDefinition
from .filesystem.configuration import FilesystemMemoryConfiguration
from .mem0_oss import DEFINITION as MEM0_OSS
from .mem0_platform import DEFINITION as MEM0_PLATFORM

# Document-only storage: the Host binds an authorized Environment file operator,
# so there is no credential and no record backend to open.
FILESYSTEM = MemoryProviderDefinition(
    type="filesystem",
    display_name="File-based",
    configuration_model=FilesystemMemoryConfiguration,
    supports_documents=True,
    supports_revisions=True,
)

BUILT_IN_MEMORY_PROVIDERS = (FILESYSTEM, MEM0_OSS, MEM0_PLATFORM)
