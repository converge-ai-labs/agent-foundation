"""boxd shared implementation over the official asynchronous SDK."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

from a13n_harness.environment.errors import EnvironmentProviderError
from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.environment.errors import EnvironmentProviderOutcomeCertainty as Certainty
from a13n_harness.environment.models import EnvironmentError, EnvironmentState
from a13n_harness.providers.environment.native.configuration import CommandConfiguration, TargetState, TokenCredential
from a13n_harness.providers.environment.native.environment import NativeReference, NativeRuntime
from a13n_harness.providers.environment.native.errors import failure

if TYPE_CHECKING:
    from boxd import AsyncBoxd, Machine

PROVIDER_KEY = "boxd"
# The public API endpoint is fixed so an ambient BOXD_BASE_URL never redirects a configured account.
API_URL = "https://boxd.sh:9443"
SIZES: dict[str, tuple[int, str]] = {"1x4": (1, "4G"), "2x8": (2, "8G"), "4x16": (4, "16G"), "8x32": (8, "32G")}


class BoxdConnectionConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    region: Literal["fra", "nyc"] = Field(description="boxd region that owns the machines.")
    organization: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        description="boxd organization owning the machines. Defaults to the API key's organization.",
    )


class BoxdEnvironmentConfiguration(CommandConfiguration):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        json_schema_extra={"x-primary-fields": ["size", "from_snapshot", "auto_suspend_timeout_seconds"]},
    )
    root: str = "/home/boxd"
    size: Literal["1x4", "2x8", "4x16", "8x32"] | None = Field(
        default=None, description="vCPU x GiB memory. Omitted: the organization default, or the snapshot's size."
    )
    from_snapshot: str | None = Field(default=None, min_length=1, max_length=128)
    auto_suspend_timeout_seconds: int = Field(default=0, ge=0, le=86400, description="0 disables idle standby.")
    auto_hibernate_timeout_seconds: int = Field(
        default=0, ge=0, le=604800, description="0 disables idle hibernation; a hibernated machine needs start()."
    )


@contextmanager
def sdk_errors(*, mutation: bool) -> Iterator[None]:
    """Bounded boxd errors; upstream exception text never becomes a public error."""
    from boxd import errors

    try:
        yield
    except (EnvironmentError, EnvironmentProviderError):
        raise
    except (errors.MachineFilesystemError, errors.MachineAgentOutdatedError):
        # A guest path failure or an unserved file request, not a target failure or an ownership conflict.
        raise EnvironmentError("Native file transfer failed.", code="environment_provider_failure") from None
    except (errors.AuthenticationError, errors.PermissionDeniedError):
        raise failure(PROVIDER_KEY, "provider_denied", Category.DENIED, certainty=Certainty.KNOWN) from None
    except errors.NotFoundError:
        raise failure(PROVIDER_KEY, "provider_target_missing", Category.MISSING, certainty=Certainty.KNOWN) from None
    except errors.ConflictError:
        raise failure(PROVIDER_KEY, "provider_target_conflict", Category.CONFLICT, certainty=Certainty.KNOWN) from None
    except errors.RateLimitError:
        raise failure(PROVIDER_KEY, "provider_unavailable", Category.UNAVAILABLE, certainty=Certainty.KNOWN) from None
    except errors.APIStatusError as error:
        if error.code in {"invalid_argument", "out_of_range"}:
            raise failure(PROVIDER_KEY, "provider_spec_invalid", Category.INVALID, certainty=Certainty.KNOWN) from None
        raise failure(
            PROVIDER_KEY,
            "provider_unknown_outcome" if mutation else "provider_unavailable",
            Category.UNKNOWN_OUTCOME if mutation else Category.UNAVAILABLE,
            certainty=Certainty.UNKNOWN if mutation else Certainty.KNOWN,
        ) from None
    except Exception:
        raise failure(
            PROVIDER_KEY,
            "provider_unknown_outcome" if mutation else "provider_unavailable",
            Category.UNKNOWN_OUTCOME if mutation else Category.UNAVAILABLE,
            certainty=Certainty.UNKNOWN if mutation else Certainty.KNOWN,
        ) from None


class BoxdReference(NativeReference[BoxdEnvironmentConfiguration, TargetState]):
    def __init__(
        self,
        config: BoxdEnvironmentConfiguration,
        environment_id: str,
        state: EnvironmentState | None,
        runtime: NativeRuntime,
    ):
        super().__init__(PROVIDER_KEY, config, environment_id, state, state_model=TargetState)
        assert isinstance(runtime.configuration, BoxdConnectionConfiguration)
        self.backend = runtime.configuration
        assert isinstance(runtime.credential, TokenCredential)
        self.token = runtime.credential.api_key
        self._client: AsyncBoxd | None = None

    def client(self) -> AsyncBoxd:
        if self._client is None:
            from boxd import AsyncBoxd

            # No SDK request retries: a mutation is never replayed after an uncertain dispatch.
            # The SDK reruns a command only when the machine never received it.
            self._client = AsyncBoxd(
                api_key=self.token.get_secret_value(),
                base_url=API_URL,
                region=self.backend.region,
                timeout=self.config.request_timeout_seconds + 10,
                max_retries=0,
            )
        return self._client

    async def get(self, reference: str) -> Machine | None:
        try:
            with sdk_errors(mutation=False):
                return await self.client().machines.get(reference)
        except EnvironmentProviderError as error:
            if error.code == "provider_target_missing":
                return None
            raise

    async def lookup(self) -> Machine | None:
        """Read the recorded machine, or find an interrupted allocation by its ownership labels."""
        machines = self.client().machines
        if self.target:
            machine = await self.get(self.target.target_id)
        else:
            with sdk_errors(mutation=False):
                found = await machines.list(org=self.backend.organization, labels={"a13n_environment": self.owner})
            live = [item for item in found if item.status != "destroyed"]
            if len(live) > 1:
                raise failure(self.provider_key, "provider_target_conflict", Category.CONFLICT)
            machine = live[0] if live else None
        if machine is None or machine.status == "destroyed":
            return None
        self.remember(machine)
        return machine

    def remember(self, machine: Machine) -> None:
        """Record a machine whose ownership labels came from a read of that machine."""
        self.validate_labels(machine.labels)
        self.record(machine.id)

    def record(self, machine_id: str) -> None:
        self.observe(
            TargetState(
                target_id=machine_id, environment_id=self.owner, configuration_fingerprint=self.config.fingerprint
            )
        )

    def phase(self, machine: Machine) -> Literal["running", "stopped"]:
        # Idle standby resumes on the next command, so it is a running target.
        if machine.status in {"running", "suspended"}:
            return "running"
        if machine.status in {"hibernated", "stopped"}:
            return "stopped"
        raise failure(self.provider_key, "provider_unavailable", Category.UNAVAILABLE)

    async def status(self) -> Literal["running", "stopped", "absent"]:
        machine = await self.lookup()
        return "absent" if machine is None else self.phase(machine)

    async def close_transport(self) -> None:
        if self._client is not None:
            client, self._client = self._client, None
            await client.close()
