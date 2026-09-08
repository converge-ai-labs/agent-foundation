"""Gateway application-service composition."""

from __future__ import annotations

from dataclasses import dataclass

from a13n_service.interactions.commands import InteractionCommands
from a13n_service.interactions.submissions import QueuedSubmissionService

from .a2a import A2AService
from .hosted_agui import HostedAguiService
from .native_streaming import NativeRunStreamService
from .notifications import NotificationService
from .queries import NativeInteractionQueries


@dataclass(frozen=True, slots=True)
class GatewayRuntime:
    """Application ports shared by public protocol adapters."""

    commands: InteractionCommands
    hosted_agui: HostedAguiService
    native_streams: NativeRunStreamService
    notifications: NotificationService
    queries: NativeInteractionQueries
    queued_submissions: QueuedSubmissionService
    a2a: A2AService | None


__all__ = ["GatewayRuntime"]
