"""Fresh attach-only Environment readiness testing."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Mapping
from typing import Protocol

from a13n_environment_provider import Environment, EnvironmentError, EnvironmentProviderError
from anyio import CancelScope, fail_after

from a13n_service.iam import AuthenticatedActor
from a13n_service.secrets.domain import SecretCredentialSource

from .catalog import FoundationEnvironmentProviderCatalog
from .domain import EnvironmentRevision
from .errors import EnvironmentManagementError


class EnvironmentSecretValueResolver(Protocol):
    async def resolve(
        self,
        *,
        actor: AuthenticatedActor,
        organization_id: str,
        workspace_id: str,
        credential: SecretCredentialSource,
    ) -> str: ...


class EnvironmentRuntimeBuilder(Protocol):
    def __call__(
        self,
        *,
        provider_key: str,
        credentials: Mapping[str, str],
    ) -> Awaitable[object]: ...


class EnvironmentAttachmentTester(Protocol):
    def __call__(
        self,
        *,
        actor: AuthenticatedActor,
        organization_id: str,
        workspace_id: str,
        revision: EnvironmentRevision,
    ) -> Awaitable[None]: ...


class NativeEnvironmentAttachmentTester:
    """Resolve fresh collaborators, attach to one exact target, and close locally."""

    def __init__(
        self,
        catalog: FoundationEnvironmentProviderCatalog,
        *,
        secret_resolver: EnvironmentSecretValueResolver,
        runtime_builder: EnvironmentRuntimeBuilder,
        timeout_seconds: float = 15,
        cleanup_timeout_seconds: float = 15,
    ) -> None:
        if timeout_seconds <= 0 or cleanup_timeout_seconds <= 0:
            raise ValueError("Environment attachment timeouts must be positive")
        self._catalog = catalog
        self._secret_resolver = secret_resolver
        self._runtime_builder = runtime_builder
        self._timeout_seconds = timeout_seconds
        self._cleanup_timeout_seconds = cleanup_timeout_seconds

    async def __call__(
        self,
        *,
        actor: AuthenticatedActor,
        organization_id: str,
        workspace_id: str,
        revision: EnvironmentRevision,
    ) -> None:
        validated = self._catalog.validate_connection(revision.connection)
        if validated.spec != revision.connection or validated.target_key != revision.target_key:
            raise _attachment_failure("The stored Environment connection no longer validates exactly.")
        credentials: dict[str, str] = {}
        try:
            for binding in revision.credential_bindings:
                credentials[binding.requirement_key] = await self._secret_resolver.resolve(
                    actor=actor,
                    organization_id=organization_id,
                    workspace_id=workspace_id,
                    credential=binding.credential,
                )
            runtime = await self._runtime_builder(
                provider_key=revision.connection.provider_key,
                credentials=credentials,
            )
            adapter = self._catalog.attachment(revision.connection.provider_key).create_attachment_environment(
                connection=validated.value,
                runtime=runtime,
            )
            if adapter.provider_key != revision.connection.provider_key or adapter.dump_state() is not None:
                raise _attachment_failure("The Environment attachment adapter violates its frozen identity.")
            await self._enter_and_close(adapter)
        except asyncio.CancelledError:
            raise
        except EnvironmentManagementError:
            raise
        except EnvironmentProviderError as error:
            safe = error.safe_projection()
            raise EnvironmentManagementError(safe.code, safe.message, status_code=409) from error
        except EnvironmentError as error:
            raise EnvironmentManagementError(
                error.code, "The Environment attachment failed.", status_code=409
            ) from error
        except TimeoutError as error:
            raise EnvironmentManagementError(
                "environment_attachment_timeout",
                "The Environment attachment readiness test timed out.",
                status_code=504,
            ) from error
        except Exception as error:
            raise _attachment_failure("The Environment attachment could not be prepared.") from error

    async def _enter_and_close(self, adapter: Environment) -> None:
        primary_error: BaseException | None = None
        try:
            with fail_after(self._timeout_seconds):
                await adapter.enter(
                    thread_id="thread-attachment-test",
                    run_id="run-attachment-test",
                    agent_instance_id="agent-attachment-test",
                    mount_id="workspace",
                )
                await adapter.ensure_ready(adapter.descriptor.operation_families)
                if adapter.dump_state() is not None:
                    raise _attachment_failure("The Environment attachment exported forbidden provider state.")
        except BaseException as error:
            primary_error = error
        cleanup_error: BaseException | None = None
        try:
            with CancelScope(shield=True), fail_after(self._cleanup_timeout_seconds):
                await adapter.close()
        except BaseException as error:
            cleanup_error = error
        if primary_error is not None and cleanup_error is not None:
            raise BaseExceptionGroup(
                "Environment attachment and local cleanup failed",
                [primary_error, cleanup_error],
            )
        if primary_error is not None:
            raise primary_error
        if cleanup_error is not None:
            raise cleanup_error


def _attachment_failure(message: str) -> EnvironmentManagementError:
    return EnvironmentManagementError(
        "environment_attachment_failed",
        message,
        status_code=409,
    )


__all__ = [
    "EnvironmentAttachmentTester",
    "EnvironmentRuntimeBuilder",
    "EnvironmentSecretValueResolver",
    "NativeEnvironmentAttachmentTester",
]
