"""Agent UI local metadata and content-addressed object storage."""

from .contracts import (
    ChildExecutionHead,
    CompactChildActivity,
    CompactChildDisplay,
    EnvironmentBindingHead,
    EnvironmentBindingKey,
    SafeFailure,
    Session,
    SnapshotRef,
    StoredChildCheckpoint,
    StoredEnvironmentState,
    StoredSessionContinuation,
)
from .database import Database, check_database, open_database, short_session, transaction
from .layout import StorageLayout
from .objects import ImmutableObjectStore, ObjectEnvelope, ObjectKind, ObjectRef
from .repositories import (
    ChildExecutionRepository,
    ConfigurationRepository,
    EnvironmentStateRepository,
    SessionRepository,
)
from .runtime import LocalStore, open_local_store

__all__ = [
    "ChildExecutionHead",
    "ChildExecutionRepository",
    "CompactChildActivity",
    "CompactChildDisplay",
    "ConfigurationRepository",
    "Database",
    "EnvironmentBindingHead",
    "EnvironmentBindingKey",
    "EnvironmentStateRepository",
    "ImmutableObjectStore",
    "LocalStore",
    "ObjectEnvelope",
    "ObjectKind",
    "ObjectRef",
    "SafeFailure",
    "Session",
    "SessionRepository",
    "SnapshotRef",
    "StorageLayout",
    "StoredChildCheckpoint",
    "StoredEnvironmentState",
    "StoredSessionContinuation",
    "check_database",
    "open_database",
    "open_local_store",
    "short_session",
    "transaction",
]
