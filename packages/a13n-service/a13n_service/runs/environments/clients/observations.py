"""Client operation evidence scoped to one tool invocation, including concurrent tools."""

from contextvars import ContextVar
from dataclasses import dataclass, field

from a13n_envd_client import EIPSession
from a13n_harness.environment.models import EnvironmentError
from a13n_harness.environment.sources import EnvironmentScope


@dataclass(slots=True)
class Observation:
    successes: set[str] = field(default_factory=set)
    disconnected: dict[str, str] = field(default_factory=dict)
    sessions_lost: dict[str, str] = field(default_factory=dict)


current: ContextVar[Observation | None] = ContextVar("client_tool_observation", default=None)


@dataclass(slots=True)
class Incarnation:
    device_id: str
    epoch: str | None = None
    scope_id: str | None = None
    session_failed: bool = False
    retry_activation: bool = False
    session: EIPSession | None = field(default=None, repr=False)

    def observe(self, event: str, _scope: EnvironmentScope, error: BaseException | None) -> None:
        if event in {"ready", "failed"}:
            self.retry_activation = isinstance(error, EnvironmentError) and error.details.get("reason") in {
                "client_offline",
                "client_discovery_unavailable",
            }
