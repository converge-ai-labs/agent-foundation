"""Seed an Agent grant in an owned lab; the public IAM API cannot create these yet."""

import argparse
import asyncio
from datetime import UTC, datetime

from a13n_service.agents.models import AgentRecord
from a13n_service.configuration.sources import load_settings
from a13n_service.iam.management.bindings import grant_role
from a13n_service.iam.models import RoleBindingRecord, UserRecord
from a13n_service.storage import open_storage, transaction
from sqlalchemy import select

from ..infrastructure.config import load_config


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("principal_id")
    parser.add_argument("agent_id")
    parser.add_argument("role", choices=("viewer", "runner", "builder"))
    args = parser.parse_args()
    config = load_config()
    assert config.get("run_faults", {}).get("skills")
    async with open_storage(load_settings().storage_settings()) as storage:
        async with transaction(storage.sessions) as session:
            agent = await session.get(AgentRecord, args.agent_id)
            user = await session.get(UserRecord, args.principal_id)
            assert agent is not None and agent.workspace_id == config["workspace_id"]
            assert agent.organization_id == config["organization_id"]
            assert user is not None
            assert await session.scalar(
                select(RoleBindingRecord.id).where(
                    RoleBindingRecord.principal_id == args.principal_id,
                    RoleBindingRecord.principal_type == "user",
                    RoleBindingRecord.resource_type == "organization",
                    RoleBindingRecord.resource_id == config["organization_id"],
                )
            )
            await grant_role(
                session,
                organization_id=config["organization_id"],
                workspace_id=config["workspace_id"],
                principal_type="user",
                principal_id=args.principal_id,
                resource_type="agent",
                resource_id=args.agent_id,
                role_key=args.role,
                created_by_user_id=config["user_id"],
                now=datetime.now(UTC),
            )


if __name__ == "__main__":
    asyncio.run(main())
