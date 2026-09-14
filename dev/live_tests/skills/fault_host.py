"""Opt-in Skill barriers outside SQL sessions; every wrapper calls the real operation."""

import os
from functools import wraps


def install(faults):
    from a13n_service.agents.invocation_resolution.preparation import AgentInvocationPreparer
    from a13n_service.agents.revisions import AgentRevisions
    from a13n_service.interactions.acceptance import RunAcceptanceService
    from a13n_service.skills.catalog import SkillCatalogService
    from a13n_service.skills.materialization import EnvironmentSkillMaterializer

    original_delete = SkillCatalogService.delete

    @wraps(original_delete)
    async def delete(self, **kwargs):
        await faults.reach("skill.before_delete", skill_id=kwargs["skill_id"])
        return await original_delete(self, **kwargs)

    SkillCatalogService.delete = delete
    original_prepare = AgentInvocationPreparer.prepare

    @wraps(original_prepare)
    async def prepare(self, **kwargs):
        result = await original_prepare(self, **kwargs)
        await faults.reach("skill.invocation_prepared", agent_id=kwargs["agent_id"])
        return result

    AgentInvocationPreparer.prepare = prepare
    original_binding = AgentRevisions._prepare_resolution

    @wraps(original_binding)
    async def binding(self, **kwargs):
        result = await original_binding(self, **kwargs)
        await faults.reach("skill.binding_prepared", agent_id=kwargs["agent"].id)
        return result

    AgentRevisions._prepare_resolution = binding
    original_initial = RunAcceptanceService._publish_initial

    @wraps(original_initial)
    async def initial(self, run, state):
        result = await original_initial(self, run, state)
        await faults.reach("skill.acceptance_prepared", agent_id=run.agent_id)
        return result

    RunAcceptanceService._publish_initial = initial
    original_publish = EnvironmentSkillMaterializer._publish

    @wraps(original_publish)
    async def publish(self, files, destination, content):
        result = await original_publish(self, files, destination, content)
        await faults.reach("skill.file_published", filename=destination.rsplit("/", 1)[-1], pid=os.getpid())
        return result

    EnvironmentSkillMaterializer._publish = publish

    from a13n_service.skills.sources import SkillSourcePreparer

    original_source = SkillSourcePreparer.prepare

    @wraps(original_source)
    async def source(self, **kwargs):
        result = await original_source(self, **kwargs)
        await faults.reach("skill.source_prepared", upload_id=result.upload_id or "github")
        return result

    SkillSourcePreparer.prepare = source

    if faults.role == "worker":
        import json
        import traceback

        from a13n_service.process.attempts import WorkerAttempts

        original_attempt = WorkerAttempts.run

        @wraps(original_attempt)
        async def attempt(self, context, *args, **kwargs):
            try:
                return await original_attempt(self, context, *args, **kwargs)
            except Exception as error:
                # Record types and locations only: exception messages can contain credentials.
                def describe(value):
                    return {
                        "type": type(value).__name__,
                        "frames": [
                            {"file": frame.filename, "line": frame.lineno, "function": frame.name}
                            for frame in traceback.extract_tb(value.__traceback__)
                        ],
                        "children": [describe(child) for child in value.exceptions]
                        if isinstance(value, BaseExceptionGroup)
                        else [],
                        "cause": describe(value.__cause__) if value.__cause__ is not None else None,
                    }

                path = faults.root / ("attempt-error-" + context.run_attempt_id + ".json")
                path.write_text(
                    json.dumps(
                        {"run_id": context.run_id, "attempt_id": context.run_attempt_id, "error": describe(error)}
                    )
                )
                path.chmod(0o600)
                raise

        WorkerAttempts.run = attempt
