"""Backpressured Run Stream publication in the executor root task."""

from __future__ import annotations

from typing import Any

from a13n_harness import HarnessEvent, HarnessRunResultEvent
from anyio import fail_after

from a13n_service.interactions.attempts import AttemptAuthorityError, AttemptContext
from a13n_service.interactions.environment_observation import EnvironmentHookObservation

from .agui import HarnessAguiRunStreamWriter
from .domain import PublicationRejected, RunStreamEvent, deterministic_run_stream_event_id
from .redis import RedisRunStream


class AttemptRunStreamProjector:
    def __init__(self, stream: RedisRunStream, context: AttemptContext) -> None:
        self._stream = stream
        self._context = context
        self._writers: dict[str, HarnessAguiRunStreamWriter] = {}
        self._harness_run_id: str | None = None
        self._environment: list[EnvironmentHookObservation] = []
        self._incomplete = False
        self._closed = False
        self._authority_lost = False

    def project_environment(self, observation: EnvironmentHookObservation) -> None:
        if self._closed:
            return
        if len(self._environment) >= 64:
            self._incomplete = True
        else:
            self._environment.append(observation)

    async def project(self, event: HarnessEvent | HarnessRunResultEvent[Any]) -> None:
        if self._authority_lost:
            raise AttemptAuthorityError("Run Stream publication authority was replaced")
        context = self._context
        if self._harness_run_id is None:
            if event.thread_id != context.thread_id:
                raise ValueError("First Harness observation must name the owning Thread")
            self._harness_run_id = event.run_id
        writer = self._writers.get(event.run_id)
        if writer is None:
            writer = HarnessAguiRunStreamWriter(
                self._stream,
                organization_id=context.organization_id,
                run_id=context.run_id,
                thread_id=context.thread_id,
                run_attempt_id=context.run_attempt_id,
                attempt_number=context.attempt_number,
                harness_run_id=event.run_id,
                source_thread_id=event.thread_id,
            )
            self._writers[event.run_id] = writer
        try:
            with fail_after(context.reconciliation_timeout.total_seconds()):
                await self._flush_environment()
                await writer.write(event)
        except PublicationRejected as error:
            self._authority_lost = True
            raise AttemptAuthorityError("Run Stream publication authority was replaced") from error
        except BaseException:
            self._incomplete = True
            raise

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._authority_lost:
            return
        try:
            await self._complete()
        except PublicationRejected as error:
            self._authority_lost = True
            raise AttemptAuthorityError("Run Stream publication authority was replaced") from error

    async def _complete(self) -> None:
        context = self._context
        with fail_after(context.reconciliation_timeout.total_seconds()):
            try:
                await self._flush_environment()
            except PublicationRejected:
                raise
            except Exception:
                self._incomplete = True
            if self._incomplete:
                await self._stream.mark_incomplete(
                    context.organization_id,
                    context.run_id,
                    run_attempt_id=context.run_attempt_id,
                    attempt_number=context.attempt_number,
                )
            elif self._harness_run_id is not None:
                await self._stream.complete_attempt_projection(
                    context.organization_id,
                    context.run_id,
                    run_attempt_id=context.run_attempt_id,
                    attempt_number=context.attempt_number,
                    harness_run_id=self._harness_run_id,
                )

    async def _flush_environment(self) -> None:
        context = self._context
        while self._environment:
            item = self._environment.pop(0)
            if item.thread_id != context.thread_id:
                raise ValueError("Environment observation names another Thread")
            await self._stream.append(
                context.organization_id,
                RunStreamEvent(
                    event_id=deterministic_run_stream_event_id(
                        "environment",
                        context.run_attempt_id,
                        item.harness_run_id,
                        item.mount_id,
                        item.event_type,
                    ),
                    event_type=item.event_type,
                    run_id=context.run_id,
                    thread_id=context.thread_id,
                    run_attempt_id=context.run_attempt_id,
                    harness_run_id=item.harness_run_id,
                    occurred_at=item.occurred_at,
                    payload=dict(item.payload),
                ),
                attempt_number=context.attempt_number,
            )
