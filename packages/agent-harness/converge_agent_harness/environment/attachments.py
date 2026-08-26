from __future__ import annotations

from converge_agent_environment_provider import (
    DirectLocalEnvironmentAttachment,
    EIPEnvironmentAttachment,
    EnvironmentRuntimeAttachment,
)

from .eip import EIPEnvironmentProviderBinding
from .local import DirectLocalEnvironmentConfiguration, DirectLocalEnvironmentProviderBinding
from .models import EnvironmentError
from .providers import EnvironmentProviderBinding


def create_environment_provider_binding(
    attachment: EnvironmentRuntimeAttachment,
) -> EnvironmentProviderBinding:
    """Exhaustively claim one provider attachment and create a fresh Harness binding."""
    if isinstance(attachment, DirectLocalEnvironmentAttachment):
        _claim(attachment)
        if not isinstance(attachment.configuration, DirectLocalEnvironmentConfiguration):
            raise EnvironmentError(
                "Direct Local attachment configuration is incompatible",
                code="environment_request_invalid",
            )
        if attachment.configuration.environment_id != attachment.environment_id:
            raise EnvironmentError(
                "Direct Local attachment environment identity is inconsistent",
                code="environment_stale_binding",
            )
        return DirectLocalEnvironmentProviderBinding(attachment.configuration)
    if isinstance(attachment, EIPEnvironmentAttachment):
        _claim(attachment)
        return EIPEnvironmentProviderBinding(
            environment_id=attachment.environment_id,
            session_source=attachment.session_source,
        )
    raise TypeError(f"unsupported Environment runtime attachment: {type(attachment).__name__}")


def _claim(attachment: DirectLocalEnvironmentAttachment | EIPEnvironmentAttachment) -> None:
    try:
        attachment.claim()
    except RuntimeError as error:
        raise EnvironmentError(
            "Environment runtime attachment was already claimed",
            code="environment_conflict",
        ) from error
