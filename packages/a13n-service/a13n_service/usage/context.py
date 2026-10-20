"""Latest main-agent usage without replaying transcript pages or summing consumption."""

from sqlalchemy import BigInteger, func, select

from a13n_service.infra.db import Storage, short_session
from a13n_service.infra.errors import not_found
from a13n_service.resources.agents.schemas import AgentConfig, apply_override
from a13n_service.resources.agents.tables import AgentRevisionRow
from a13n_service.resources.models.schemas import ModelConfig
from a13n_service.resources.models.tables import ModelRow
from a13n_service.runs.schemas import RunOptions
from a13n_service.runs.tables import AttemptRow, RunRow, UsageRecordRow
from a13n_service.runs.threads import get_run, get_thread
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import Principal
from a13n_service.usage.schemas import ContextUsage, ContextUsageQuery


async def read_context_usage(
    storage: Storage, actor: Principal, workspace_id: str, query: ContextUsageQuery
) -> ContextUsage | None:
    record = UsageRecordRow.record
    statement = (
        select(UsageRecordRow, RunRow, ModelRow, AgentRevisionRow)
        .join(RunRow, RunRow.id == UsageRecordRow.run_id)
        .join(AttemptRow, AttemptRow.id == UsageRecordRow.run_attempt_id)
        .join(AgentRevisionRow, AgentRevisionRow.id == RunRow.agent_revision_id)
        .outerjoin(ModelRow, ModelRow.id == UsageRecordRow.model_id)
        .where(UsageRecordRow.workspace_id == workspace_id, record["kind"].astext == "model")
    )
    async with short_session(storage) as session:
        await workspace_scope(session, actor, workspace_id, "read")
        selected = None
        if query.record_id is not None:
            statement = statement.where(UsageRecordRow.id == query.record_id)
        else:
            if query.thread_id is not None:
                await get_thread(session, workspace_id, query.thread_id)
                selected = await session.scalar(
                    select(RunRow)
                    .where(RunRow.workspace_id == workspace_id, RunRow.thread_id == query.thread_id)
                    .order_by(RunRow.created_at.desc(), RunRow.id.desc())
                    .limit(1)
                )
                if selected is None:
                    return None
            else:
                assert query.run_id is not None
                selected = await get_run(session, workspace_id, query.run_id)
            statement = statement.where(RunRow.id == selected.id)
            statement = statement.where(
                record["parent_agent_instance_id"].astext.is_(None),
                record["delegation_id"].astext.is_(None),
                func.coalesce(record["source"].astext, "agent") == "agent",
                func.coalesce(record["outcome"].astext, "completed") == "completed",
            ).order_by(
                RunRow.created_at.desc(),
                RunRow.id.desc(),
                AttemptRow.number.desc(),
                record["response_ordinal"].astext.cast(BigInteger).desc(),
                UsageRecordRow.id.desc(),
            )
        row = (await session.execute(statement.limit(1))).one_or_none()
        if row is None:
            if query.record_id is not None:
                raise not_found("usage_record", query.record_id)
            assert selected is not None
            run = selected
            revision = await session.get_one(AgentRevisionRow, run.agent_revision_id)
            contribution, model = None, None
        else:
            contribution, run, model, revision = row
        observed = contribution.record if contribution is not None else {}
        capacity = ModelConfig.model_validate(model.config).characteristics.context_window_tokens if model else None
        # An inline child does not inherit its parent's context policy.
        if (
            not observed.get("parent_agent_instance_id")
            and not observed.get("delegation_id")
            and observed.get("source", "agent") == "agent"
        ):
            config = AgentConfig.model_validate(revision.config)
            override = RunOptions.model_validate(run.options).overrides
            if override is not None:
                config = apply_override(config, override)
            capacity = config.model_characteristics.context_window_tokens
            if capacity is None:
                configured_model = await session.scalar(
                    select(ModelRow).where(ModelRow.workspace_id == workspace_id, ModelRow.key == config.model)
                )
                capacity = (
                    ModelConfig.model_validate(configured_model.config).characteristics.context_window_tokens
                    if configured_model
                    else None
                )
        usage = observed.get("request_usage", {})
        known = observed.get("usage_status") != "unavailable" and any(
            usage.get(key, 0) > 0
            for key in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")
        )
        return ContextUsage(
            run_id=run.id,
            record_id=contribution.id if contribution is not None else None,
            input_tokens=usage.get("input_tokens", 0) if known else None,
            output_tokens=usage.get("output_tokens", 0) if known else None,
            context_window_tokens=capacity,
        )
