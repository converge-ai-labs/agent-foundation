"""Secret references, persistence, and protection contracts."""

from .crypto import EncryptedSecret, SecretProtectionError, SecretProtector
from .domain import (
    InvokingUserSecretCredential,
    SecretCredentialSource,
    SecretOwnerType,
    WorkspaceSecretCredential,
)
from .models import SecretRecord

__all__ = [
    "EncryptedSecret",
    "InvokingUserSecretCredential",
    "SecretCredentialSource",
    "SecretOwnerType",
    "SecretProtectionError",
    "SecretProtector",
    "SecretRecord",
    "WorkspaceSecretCredential",
]
