"""Direct Pod lifecycle, paced at durable resource boundaries."""

from __future__ import annotations

from datetime import datetime, timedelta

from a13n_harness.environment.errors import (
    EnvironmentProviderError,
    provider_error,
)
from a13n_harness.environment.errors import (
    EnvironmentProviderErrorCategory as Category,
)
from a13n_harness.environment.errors import (
    EnvironmentProviderOutcomeCertainty as Certainty,
)
from a13n_harness.environment.models import EnvironmentError, EnvironmentState
from a13n_harness.providers.environment.kubernetes.configuration import KEY, KubernetesState, Resource, decode
from a13n_harness.providers.environment.kubernetes.http import execution
from a13n_harness.providers.environment.kubernetes.runtime import KubernetesRuntime, check_resource

from a13n_service.providers.environment.kubernetes import Diagnostics, LogQuery, Logs
from a13n_service.providers.environment.management import Completed, EnvironmentProvider, EnvironmentStatus, Pending
from a13n_service.providers.environment.metrics import MetricsLaunch
from a13n_service.providers.environment.recipes.kubernetes import KubernetesRecipe

from . import diagnostics
from .render import initial, recipe_fingerprint, render


def remember(state: KubernetesState, reference: Resource, uid: str) -> KubernetesState:
    updated = reference.model_copy(update={"uid": uid})
    return state.model_copy(
        update={
            "resources": tuple(updated if r == reference else r for r in state.resources),
            **({"pod_uid": uid} if reference.kind == "Pod" else {}),
        }
    )


class KubernetesManagement(EnvironmentProvider[KubernetesRecipe]):
    def __init__(self, runtime: KubernetesRuntime, *, owns_runtime: bool = True):
        self.runtime = runtime
        self.owns_runtime = owns_runtime

    def target(
        self, environment: object, environment_id: str, state: EnvironmentState | None
    ) -> tuple[KubernetesRecipe, KubernetesState | None]:
        recipe = KubernetesRecipe.model_validate(environment)
        target = decode(recipe, state)
        if target is not None:
            self.runtime.validate(target)
            if target.environment_id != environment_id or target.recipe_fingerprint != recipe_fingerprint(recipe):
                raise provider_error(KEY, "provider_target_conflict", Category.CONFLICT)
        return recipe, target

    async def kubernetes_diagnostics(
        self, environment: object, *, environment_id: str, state: EnvironmentState
    ) -> Diagnostics:
        recipe, target = self.target(environment, environment_id, state)
        assert target is not None
        return await diagnostics.inspect(self.runtime, recipe, target)

    async def kubernetes_logs(
        self, environment: object, *, environment_id: str, state: EnvironmentState, query: LogQuery
    ) -> Logs:
        recipe, target = self.target(environment, environment_id, state)
        assert target is not None
        return await diagnostics.logs(self.runtime, recipe, target, query)

    async def create(
        self,
        environment: object,
        *,
        environment_id: str,
        operation_id: str,
        state: EnvironmentState | None = None,
        metrics: MetricsLaunch | None = None,
    ) -> Pending | Completed:
        del operation_id
        recipe, target = self.target(environment, environment_id, state)
        if metrics is not None:
            raise provider_error(KEY, "provider_operation_unsupported", Category.UNSUPPORTED)
        if not self.runtime.profile.allow_plaintext_envd:
            raise provider_error(KEY, "kubernetes_plaintext_envd_denied", Category.DENIED)
        if recipe.connection == "port_forward" and not self.runtime.profile.allow_port_forward:
            raise provider_error(KEY, "kubernetes_port_forward_denied", Category.DENIED)
        if self.runtime.connection._bootstrap is None:
            raise provider_error(KEY, "envd_bootstrap_required", Category.INVALID)
        if target is None:
            # Persist all deterministic names before the first native mutation.
            return Pending(initial(recipe, self.runtime.connection, environment_id).envelope(), settled=True)
        for reference in target.resources:
            value = await self.runtime.get(reference.kind, reference.name)
            if reference.disposition == "borrowed":
                # Missing references are diagnosed by the native Pod; optional references
                # must not block creation. Record exact identities when they are observed.
                if value is not None:
                    check_resource(value, reference, target)
                    if reference.uid is None:
                        return Pending(remember(target, reference, value["metadata"]["uid"]).envelope(), settled=True)
                continue
            if value is None:
                if reference.uid is not None:
                    raise provider_error(KEY, "environment_not_found", Category.MISSING, certainty=Certainty.KNOWN)
                body = render(recipe, self.runtime.profile, target, reference)
                if reference.kind == "Secret":
                    body["stringData"] = {"token": self.runtime.connection._bootstrap.get_secret_value()}
                value = await self.runtime.create(reference.kind, body)
                if value is None:
                    return Pending(target.envelope())
                check_resource(value, reference, target)
                return Pending(remember(target, reference, value["metadata"]["uid"]).envelope(), settled=True)
            check_resource(value, reference, target)
            if reference.uid is None:
                return Pending(remember(target, reference, value["metadata"]["uid"]).envelope(), settled=True)
        pod = await self.runtime.pod(target)
        phase = pod.get("status", {}).get("phase", "Pending")
        if pod["metadata"].get("deletionTimestamp") or phase in {"Failed", "Succeeded"}:
            raise provider_error(KEY, "kubernetes_pod_terminated", Category.MISSING, certainty=Certainty.KNOWN)
        if phase != "Running":
            return Pending(target.model_copy(update={"stage": "pod_pending"}).envelope(), settled=True)
        # Running is a native observation, not proof of EIP identity or capabilities.
        if target.stage != "envd":
            return Pending(target.model_copy(update={"stage": "envd"}).envelope(), settled=True)
        attached = execution(self.runtime, target, recipe, environment_id)
        try:
            await attached.open(execution_id="kubernetes-readiness")
            return Completed(target.model_copy(update={"stage": "ready"}).envelope())
        except EnvironmentProviderError as error:
            if error.category not in {Category.UNAVAILABLE, Category.TIMEOUT}:
                raise
            return Pending(target.envelope(), settled=True)
        except EnvironmentError as error:
            if error.code not in {"environment_provider_failure", "environment_unavailable"}:
                raise
            return Pending(target.envelope(), settled=True)
        finally:
            await attached.close()

    async def inspect(
        self, environment: object, *, environment_id: str, state: EnvironmentState | None
    ) -> EnvironmentStatus:
        _, target = self.target(environment, environment_id, state)
        if target is None or target.pod_uid is None:
            return EnvironmentStatus("absent", state)
        try:
            pod = await self.runtime.pod(target)
        except EnvironmentProviderError as error:
            if error.category == Category.MISSING:
                return EnvironmentStatus("absent", state)
            raise
        phase = pod.get("status", {}).get("phase")
        return EnvironmentStatus(
            "running" if phase == "Running" and not pod["metadata"].get("deletionTimestamp") else "stopped", state
        )

    async def destroy(
        self, environment: object, *, environment_id: str, operation_id: str, state: EnvironmentState | None
    ) -> Pending | Completed:
        del operation_id
        _, target = self.target(environment, environment_id, state)
        if target is None:
            return Completed()
        # Pod first; only after its disappearance may claims and bootstrap be removed.
        for reference in reversed(target.resources):
            if reference.disposition == "borrowed" or (reference.disposition == "retain" and reference.uid is not None):
                # Retained claims are already receipted and require no further ownership
                # checks: operators may relabel or reuse them independently of retirement.
                continue
            value = await self.runtime.get(reference.kind, reference.name)
            if value is None:
                continue
            if reference.uid is not None and value["metadata"]["uid"] != reference.uid:
                # The old UID is absent. Never delete a replacement under its reused name.
                continue
            check_resource(value, reference, target)
            if reference.uid is None:
                return Pending(remember(target, reference, value["metadata"]["uid"]).envelope(), settled=True)
            if reference.disposition == "retain":
                continue
            if not value["metadata"].get("deletionTimestamp"):
                await self.runtime.delete(reference)
            return Pending(target.model_copy(update={"stage": "deleting"}).envelope(), settled=True)
        return Completed(target.model_copy(update={"stage": "deleted"}).envelope())

    async def start(
        self, environment: object, *, environment_id: str, operation_id: str, state: EnvironmentState | None
    ):
        raise provider_error(KEY, "provider_operation_unsupported", Category.UNSUPPORTED)

    async def stop(
        self, environment: object, *, environment_id: str, operation_id: str, state: EnvironmentState | None
    ):
        raise provider_error(KEY, "provider_operation_unsupported", Category.UNSUPPORTED)

    async def keepalive(
        self,
        environment: object,
        *,
        environment_id: str,
        state: EnvironmentState | None,
        deadline: datetime,
        operation_id: str,
    ) -> datetime | None:
        raise provider_error(KEY, "provider_operation_unsupported", Category.UNSUPPORTED)

    def keepalive_horizon(
        self, environment: object, *, environment_id: str, state: EnvironmentState | None
    ) -> timedelta:
        raise provider_error(KEY, "provider_operation_unsupported", Category.UNSUPPORTED)

    async def close(self) -> None:
        if self.owns_runtime:
            await self.runtime.close()
