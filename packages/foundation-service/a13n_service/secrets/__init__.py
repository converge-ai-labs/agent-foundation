"""Secret references, persistence, and protection contracts."""

from .crypto import EncryptedSecret, SecretProtectionError, SecretProtector
from .domain import (
    InvokingUserSecretCredential,
    SecretCredentialSource,
    SecretOperation,
    SecretOwnerType,
    SecretUseContext,
    WorkspaceSecretCredential,
)
from .models import SecretRecord
from .service import InternalSecretError, InternalSecretService, SecretValueRef

__all__ = [
    "EncryptedSecret",
    "InternalSecretError",
    "InternalSecretService",
    "InvokingUserSecretCredential",
    "SecretCredentialSource",
    "SecretOperation",
    "SecretOwnerType",
    "SecretProtectionError",
    "SecretProtector",
    "SecretRecord",
    "SecretUseContext",
    "SecretValueRef",
    "WorkspaceSecretCredential",
]
