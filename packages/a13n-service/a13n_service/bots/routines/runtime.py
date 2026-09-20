"""Bot-owned tools, injected only for authenticated human channel runs."""

from a13n_harness import AgentContext
from pydantic import BaseModel, JsonValue, ValidationError
from pydantic_ai.capabilities import MCP

from a13n_service.bots.progress.models import ProgressRecord
from a13n_service.connectivity.domain import JsonObject
from a13n_service.connectivity.execution import AttemptToolScope, ScopeGuard
from a13n_service.connectivity.native_actions import action
from a13n_service.connectivity.native_context import InboundRunContext, authorized_account
from a13n_service.connectivity.toolsets import local_capability
from a13n_service.interactions.attempts import AttemptContext
from a13n_service.interactions.models import RunRecord
from a13n_service.storage import short_session, transaction

from .context import is_group
from .domain import ListRoutines, ProposeRoutine
from .service import RoutineInputError, RoutineService


class RoutineTools:
    def __init__(self, service: RoutineService) -> None:
        self.service = service

    async def __call__(
        self, scope: AttemptToolScope, guard: ScopeGuard, attempt: AttemptContext
    ) -> MCP[AgentContext] | None:
        contexts = [
            context
            for context in scope.native_tool_contexts
            if isinstance(context, InboundRunContext) and is_group(context)
        ]
        if len(contexts) != 1:
            return None
        context = contexts[0]
        async with short_session(self.service.sessions) as session:
            run = await session.get(RunRecord, attempt.run_id)
            progress = await session.get(ProgressRecord, attempt.run_id)
            if run is None or run.trigger_type != "inbound" or progress is None or len(progress.requester_ids) != 1:
                return None

        async def propose(arguments: ProposeRoutine) -> BaseModel:
            async with transaction(self.service.sessions) as session:
                await guard(session)
                await authorized_account(
                    session,
                    actor=scope.actor,
                    organization_id=scope.organization_id,
                    workspace_id=scope.workspace_id,
                    account_id=context.account_id,
                )
                result = await self.service.propose(
                    session, run_id=attempt.run_id, context=context, arguments=arguments
                )
            return _Result(value=result)

        async def list_routines(arguments: ListRoutines) -> BaseModel:
            async with short_session(self.service.sessions) as session:
                await guard(session)
                await authorized_account(
                    session,
                    actor=scope.actor,
                    organization_id=scope.organization_id,
                    workspace_id=scope.workspace_id,
                    account_id=context.account_id,
                )
                result = await self.service.list(session, context=context, cursor=arguments.cursor)
            return _Result(value=result)

        actions = {
            item.definition.name: item
            for item in (action("propose", ProposeRoutine, propose), action("list", ListRoutines, list_routines))
        }
        actions["propose"].definition.description = (
            "Create or edit a scheduled task in this group, or propose pause/resume/delete. "
            "Only use for an explicit human scheduling request. Ask about ambiguous times/timezones. "
            "Use an explicit IANA timezone and a self-contained prompt. The creator must confirm the confirmation card; "
            "never claim the task is active before confirmation. Times are absolute at (ISO8601 with offset), "
            "or recurring time_of_day (HH:MM) plus weekdays (0=Monday..6=Sunday). "
            "For daily tasks use time_of_day and all weekdays, omitting at. "
            "Never pause or resume an unconfirmed draft. List tasks first to obtain the routine_id for changes."
        )
        actions["list"].definition.description = "List this group's scheduled tasks and pending changes/status."

        async def call(name: str, arguments: JsonObject) -> JsonValue:
            try:
                return await actions[name].call(arguments)
            except (RoutineInputError, ValidationError) as error:
                return {
                    "error": str(error) if isinstance(error, RoutineInputError) else "routine_arguments_invalid",
                    "message": "No change was applied. List this channel's tasks and copy the exact task ID for edits; "
                    "check the schedule and ask the requester if clarification is needed.",
                }

        return await local_capability(
            key="bot_routines",
            model_alias="bot_routines",
            tools=tuple(a.definition for a in actions.values()),
            allowed=tuple(actions),
            handler=call,
        )


class _Result(BaseModel):
    value: JsonObject
