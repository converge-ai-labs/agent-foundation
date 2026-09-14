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
