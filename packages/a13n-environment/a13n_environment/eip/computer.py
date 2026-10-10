from __future__ import annotations

from a13n_envd_client import EIPSession
from a13n_envd_client.eip import v1 as eip

from ..computer import (
    ComputerActionResult,
    ComputerClick,
    ComputerDescription,
    ComputerDrag,
    ComputerInput,
    ComputerMove,
    ComputerObservation,
    ComputerPressKeys,
    ComputerScreenshot,
    ComputerScroll,
    ComputerTypeText,
)
from ..models import EnvironmentError
from ._common import convert_receipt, invoke, new_context, session_client


class EIPComputerOperations:
    def __init__(self, session: EIPSession, *, execution_id: str, generation: str) -> None:
        self._session = session
        self._execution_id = execution_id
        self._generation = generation

    async def describe(self) -> ComputerDescription:
        result = await invoke(
            session_client(self._session).computer_describe(eip.ComputerDescribeParams(context=new_context()))
        )
        return ComputerDescription.model_validate(
            {**result.model_dump(), "scroll_units": result.scroll_units or ("pixels",)}
        )

    async def observe(self, *, target_id: str | None = None, max_dimension: int = 1280) -> ComputerScreenshot:
        return await invoke(self._observe(target_id=target_id, max_dimension=max_dimension))

    async def _observe(self, *, target_id: str | None, max_dimension: int) -> ComputerScreenshot:
        async with self._session.observe_computer(target_id=target_id, max_dimension=max_dimension) as reader:
            if reader.opened.size_bytes > 4 * 1024 * 1024:
                raise EnvironmentError("Computer image exceeds its size limit", code="environment_too_large")
            observation = ComputerObservation.model_validate(
                {
                    **reader.opened.observation.model_dump(),
                    "execution_id": self._execution_id,
                    "observed_generation": self._generation,
                }
            )
            data = b"".join([chunk async for chunk in reader])
            return ComputerScreenshot(observation, data)

    async def execute(self, request: ComputerInput) -> ComputerActionResult:
        try:
            return await self._execute(request)
        except EnvironmentError as error:
            if error.retry_hint == "new_run" and "dispatch_stage" not in error.details:
                raise EnvironmentError(
                    "Computer input outcome is unconfirmed",
                    code=error.code,
                    retry_hint="reconcile_first",
                    details={
                        **error.details,
                        "dispatch_stage": "unknown",
                        "hint": (
                            "The connection or response failed; input may already have affected the desktop. "
                            "Reconnect if needed and inspect a fresh observation before another action. "
                            "Do not automatically replay input; a new Run or Session does not undo it."
                        ),
                    },
                ) from error
            raise

    async def _execute(self, request: ComputerInput) -> ComputerActionResult:
        client = session_client(self._session)
        context = new_context()
        if isinstance(request, ComputerClick | ComputerMove | ComputerDrag | ComputerScroll):
            observation = request.observation
            if (observation.execution_id, observation.observed_generation) != (self._execution_id, self._generation):
                raise EnvironmentError("Computer observation is stale", code="environment_stale_mount")
        if isinstance(request, ComputerClick):
            result = await invoke(
                client.computer_click(
                    eip.ComputerClickParams(
                        context=context,
                        observation_id=request.observation.observation_id,
                        point=eip.ComputerPoint(x=request.point.x, y=request.point.y),
                        button=eip.ComputerButton(request.button),
                        count=request.count,
                    )
                )
            )
        elif isinstance(request, ComputerMove):
            result = await invoke(
                client.computer_move(
                    eip.ComputerMoveParams(
                        context=context,
                        observation_id=request.observation.observation_id,
                        point=eip.ComputerPoint(x=request.point.x, y=request.point.y),
                    )
                )
            )
        elif isinstance(request, ComputerDrag):
            result = await invoke(
                client.computer_drag(
                    eip.ComputerDragParams(
                        context=context,
                        observation_id=request.observation.observation_id,
                        start=eip.ComputerPoint(x=request.start.x, y=request.start.y),
                        end=eip.ComputerPoint(x=request.end.x, y=request.end.y),
                        button=eip.ComputerButton(request.button),
                        duration_ms=request.duration_ms,
                    )
                )
            )
        elif isinstance(request, ComputerScroll):
            if request.unit == "steps" and "steps" not in (await self.describe()).scroll_units:
                raise EnvironmentError("Provider does not support step scrolling", code="environment_unsupported")
            result = await invoke(
                client.computer_scroll(
                    eip.ComputerScrollParams(
                        context=context,
                        observation_id=request.observation.observation_id,
                        point=eip.ComputerPoint(x=request.point.x, y=request.point.y),
                        delta_x=request.delta_x,
                        delta_y=request.delta_y,
                        unit=eip.ComputerScrollUnit.STEPS if request.unit == "steps" else None,
                    )
                )
            )
        elif isinstance(request, ComputerTypeText):
            result = await invoke(
                client.computer_type_text(eip.ComputerTypeTextParams(context=context, text=request.text))
            )
        elif isinstance(request, ComputerPressKeys):
            result = await invoke(
                client.computer_press_keys(eip.ComputerPressKeysParams(context=context, keys=request.keys))
            )
        else:
            raise EnvironmentError("Unsupported computer input", code="environment_request_invalid")
        return ComputerActionResult(
            receipt=convert_receipt(
                result.receipt, session=self._session, execution_id=self._execution_id, generation=self._generation
            ),
            effect=result.effect.value,
            input_cleanup_complete=result.input_cleanup_complete,
        )
