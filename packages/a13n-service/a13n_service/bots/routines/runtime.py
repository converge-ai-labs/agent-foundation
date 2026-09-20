"""Bot-owned tools, injected only for authenticated human channel runs."""

from a13n_harness import AgentContext
from pydantic import BaseModel, JsonValue, ValidationError
from pydantic_ai.capabilities import MCP
from sqlalchemy import func, select, tuple_

from a13n_service.bots.connectivity.models import BotCheckRecord
from a13n_service.bots.progress.authority import ProgressUnavailable
from a13n_service.bots.progress.models import ProgressRecord
from a13n_service.connectivity.accounts.models import AccountRecord
from a13n_service.connectivity.accounts.target_models import AccountTargetRecord
from a13n_service.connectivity.domain import JsonObject
from a13n_service.connectivity.execution import AttemptToolScope, ScopeGuard
from a13n_service.connectivity.native_actions import action
from a13n_service.connectivity.native_context import InboundRunContext, authorized_account
from a13n_service.connectivity.providers.registry import event_subscription_adapters
from a13n_service.connectivity.toolsets import local_capability
from a13n_service.iam import AuthorizationError
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
            source_agent_id = run.agent_id

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

        async def event_sources(arguments: ListRoutines) -> BaseModel:
            async with short_session(self.service.sessions) as session:
                await guard(session)
                destination = await authorized_account(
                    session,
                    actor=scope.actor,
                    organization_id=scope.organization_id,
                    workspace_id=scope.workspace_id,
                    account_id=context.account_id,
                )
                adapters = event_subscription_adapters()
                query = (
                    select(AccountTargetRecord, AccountRecord)
                    .join(
                        AccountRecord,
                        AccountRecord.id == AccountTargetRecord.account_id,
                    )
                    .where(
                        AccountRecord.organization_id == destination.organization_id,
                        AccountRecord.workspace_id == destination.workspace_id,
                        tuple_(AccountRecord.provider_key, AccountRecord.provider_config_version).in_(adapters),
                        AccountRecord.status == "active",
                        AccountRecord.deleted_at.is_(None),
                        AccountRecord.receive_enabled.is_(True),
                        AccountTargetRecord.receive_enabled.is_(True),
                        AccountRecord.execution_service_account_id == scope.actor.principal.principal_id,
                        func.coalesce(AccountTargetRecord.agent_id, AccountRecord.default_agent_id) == source_agent_id,
                    )
                )
                if arguments.cursor:
                    query = query.where(AccountTargetRecord.id > arguments.cursor)
                candidates = (await session.execute(query.order_by(AccountTargetRecord.id).limit(21))).all()
                items: list[JsonValue] = []
                for target, source in candidates[:20]:
                    adapter = adapters[(source.provider_key, source.provider_config_version)]
                    if target.target_kind != adapter.target_kind:
                        continue
                    try:
                        await authorized_account(
                            session,
                            actor=scope.actor,
                            organization_id=scope.organization_id,
                            workspace_id=scope.workspace_id,
                            account_id=source.id,
                        )
                    except AuthorizationError:
                        continue
                    check = await session.get(BotCheckRecord, (source.id, target.external_target_id))
                    conversation = check.result_json.get("conversation") if check else None
                    name = conversation.get("name") if isinstance(conversation, dict) else None
                    items.append(
                        {
                            "source_target_id": target.id,
                            "account_name": source.name,
                            "provider_key": source.provider_key,
                            "target_kind": target.target_kind,
                            "external_target_id": target.external_target_id,
                            "target_name": name,
                            "event_types": [event_type.describe() for event_type in adapter.event_types],
                            "setup": adapter.setup,
                        }
                    )
                return _Result(
                    value={
                        "items": items,
                        "next_cursor": candidates[19][0].id if len(candidates) > 20 else None,
                        "setup": "Sources must be configured targets using this bot's Agent and execution service account. "
                        "If target_name is absent, ask an administrator to verify the target in Bot setup; never guess its identity.",
                    }
                )

        actions = {
            item.definition.name: item
            for item in (
                action("propose", ProposeRoutine, propose),
                action("list", ListRoutines, list_routines),
                *([action("event_sources", ListRoutines, event_sources)] if context.provider_key == "slack" else []),
            )
        }
        actions["propose"].definition.description = (
            "Create or edit a task in this conversation, or propose pause/resume/delete. "
            "Only use for an explicit human request. Ask about ambiguous times/timezones. "
            "Calendar schedules require an explicit IANA timezone. All tasks need a self-contained prompt. The creator must confirm the card; "
            "never claim the task is active before confirmation. Times are absolute at (ISO8601 with offset), "
            "or recurring time_of_day (HH:MM) plus weekdays (0=Monday..6=Sunday). "
            "For daily tasks use time_of_day and all weekdays, omitting at. "
            "Never pause or resume an unconfirmed draft. "
            "List tasks first to obtain the routine_id for changes. "
        )
        if "event_sources" in actions:
            actions["propose"].definition.description += (
                "For events, call event_sources first and use only a returned source_target_id; never infer source identity. "
                "Choose event instead of schedule. Copy an advertised event_type and provide filters matching its filter_schema. "
                "Set once=true for one accepted occurrence, or once=false for ongoing monitoring. "
                "Follow the source's setup requirements. Confirm that sharing source information "
                "to this channel is intended. Event matching is automatic; the prompt describes what to do after a match."
            )
        actions["list"].definition.description = "List this conversation's tasks and pending changes/status."
        if "event_sources" in actions:
            actions[
                "event_sources"
            ].definition.description = "List authorized event sources, supported event types, strict filter schemas, and setup requirements. Call before proposing a subscription."

        async def call(name: str, arguments: JsonObject) -> JsonValue:
            try:
                return await actions[name].call(arguments)
            except (RoutineInputError, ValidationError, ProgressUnavailable, AuthorizationError) as error:
                return {
                    "error": str(error)
                    if isinstance(error, (RoutineInputError, ProgressUnavailable))
                    else "routine_arguments_invalid",
                    "message": "No change was applied. List this channel's tasks and copy the exact task ID for edits; "
                    "check the schedule or authorized event source and ask the requester if clarification is needed.",
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
