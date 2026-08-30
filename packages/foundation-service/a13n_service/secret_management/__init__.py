"""Managed Secret persistence and protection contracts."""

from .crypto import EncryptedSecret, SecretProtectionError, SecretProtector
from .models import ManagedSecretRecord

__all__ = ["EncryptedSecret", "ManagedSecretRecord", "SecretProtectionError", "SecretProtector"]
