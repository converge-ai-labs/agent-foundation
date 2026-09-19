"""Built-in Memory definitions use the same authoring contract as installed providers."""

from ..authentication import Authentication, CredentialMode
from .definition import MemoryProviderDefinition
from .filesystem.configuration import FilesystemMemoryConfiguration, FilesystemMemoryCredential
from .mem0_oss import DEFINITION as MEM0_OSS
from .mem0_platform import DEFINITION as MEM0_PLATFORM

# Document-only storage: the Host binds an authorized Environment file operator,
# so there is no credential and no record backend to open.
FILESYSTEM = MemoryProviderDefinition(
    type="filesystem",
    display_name="File-based",
    configuration_model=FilesystemMemoryConfiguration,
    credential_model=FilesystemMemoryCredential,
    authentication=Authentication(mode=CredentialMode.forbidden),
    supports_documents=True,
    supports_records=False,
    supports_revisions=True,
)

BUILT_IN_MEMORY_PROVIDERS = (FILESYSTEM, MEM0_OSS, MEM0_PLATFORM)
