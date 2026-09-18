"""Subject permission hints reuse real IAM without opening memory backends."""

from dataclasses import replace

import httpx2
import pytest
from a13n_harness.providers.memory.mem0_oss import Mem0OSSBackend
from a13n_service.iam import AuthorizationError, PrincipalRef
from a13n_service.iam.models import RoleBindingRecord
from a13n_service.interactions.models import ThreadRecord
from a13n_service.memory.domain import MemoryScope
from a13n_service.storage import transaction

from ..hooks.support import RUN_ID, hook_actor, seed_hook_actor_access, seed_run_and_secret
from ..interactions.conftest import AGENT_ID, NOW, ORGANIZATION_ID, THREAD_ID, USER_ID, WORKSPACE_ID
from .support import memory_service

pytestmark = pytest.mark.anyio


async def test_subject_access_follows_direct_agent_grants_and_current_thread_head(interaction_sessions):
    sessions = interaction_sessions
    await seed_hook_actor_access(sessions)
    await seed_run_and_secret(sessions)

    def no_io(request):
        raise AssertionError("Permission projection must not call the backend")

    async with httpx2.AsyncClient(base_url="http://memory/", transport=httpx2.MockTransport(no_io)) as client:
        service, provider, plugin = await memory_service(
            sessions, Mem0OSSBackend(client), principal=hook_actor(), workspace_id=WORKSPACE_ID
        )
        kwargs = {"actor": hook_actor(), "workspace_id": WORKSPACE_ID, "provider_id": provider.id}
        async with transaction(sessions) as session:
            workspace_binding = await session.get(RoleBindingRecord, "rb_hookws717171717")
            await session.delete(workspace_binding)
            session.add(
                RoleBindingRecord(
                    id="rb_memorydirect123",
                    organization_id=ORGANIZATION_ID,
                    workspace_id=WORKSPACE_ID,
                    principal_type="user",
                    principal_id=USER_ID,
                    resource_type="agent",
                    resource_id=AGENT_ID,
                    role_key="builder",
                    created_by_user_id=USER_ID,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
        for scope, subject in (("agent", AGENT_ID), ("thread", THREAD_ID)):
            assert (await service.access(**kwargs, selection=MemoryScope(scope=scope, subject_id=subject))).can_write
        with pytest.raises(AuthorizationError):
            await service.access(**kwargs, selection=MemoryScope(scope="user"))
        async with transaction(sessions) as session:
            binding = await session.get(RoleBindingRecord, "rb_memorydirect123")
            binding.role_key = "viewer"
        for scope, subject in (("agent", AGENT_ID), ("thread", THREAD_ID)):
            assert not (
                await service.access(**kwargs, selection=MemoryScope(scope=scope, subject_id=subject))
            ).can_write
        async with transaction(sessions) as session:
            thread = await session.get(ThreadRecord, THREAD_ID)
            thread.current_run_id = None
            thread.head_run_id = RUN_ID
        # A historical head never restores current-Thread access for a direct-only actor.
        with pytest.raises(AuthorizationError):
            await service.access(**kwargs, selection=MemoryScope(scope="thread", subject_id=THREAD_ID))
        with pytest.raises(AuthorizationError):
            await service.access(**kwargs, selection=MemoryScope(scope="agent", subject_id="agt_missing1234567890"))
        service_actor = replace(
            hook_actor(), principal=PrincipalRef(principal_type="service_account", principal_id="sa_missing1234567890")
        )
        with pytest.raises(AuthorizationError):
            await service.access(**{**kwargs, "actor": service_actor}, selection=MemoryScope(scope="user"))
        assert plugin.opened == 0
