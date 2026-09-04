"""Gateway application-service composition."""

from __future__ import annotations

from dataclasses import dataclass

from .commands import NativeInteractionCommands
from .hosted_agui import HostedAguiService
from .native_streaming import NativeRunStreamService
from .notifications import NotificationService
from .queries import NativeInteractionQueries


@dataclass(frozen=True, slots=True)
class GatewayRuntime:
    """Application ports shared by public protocol adapters."""

    commands: NativeInteractionCommands
    hosted_agui: HostedAguiService
    native_streams: NativeRunStreamService
    notifications: NotificationService
    queries: NativeInteractionQueries


__all__ = ["GatewayRuntime"]
