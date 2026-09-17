"""Read only Skill lock projections from persisted Run state in an owned lab."""

import argparse
import asyncio

from a13n_service.agents.domain import EffectiveAgentConfig
from a13n_service.configuration.sources import load_settings
from a13n_service.interactions.models import RunRecord
from a13n_service.interactions.objects import RunStateStore
from a13n_service.storage import open_storage, short_session

from ..infrastructure.config import CONFIG, load_config
from ..infrastructure.round_two_lab import private_json


def project(config: EffectiveAgentConfig):
    return {
        "skills": [item.model_dump(mode="json") for item in config.skills],
        "children": {key: project(child.effective_config) for key, child in config.child_configs.items()},
    }


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_id")
    args = parser.parse_args()
    config = load_config()
    assert config.get("run_faults", {}).get("skills")
    async with open_storage(load_settings().storage_settings()) as storage:
        async with short_session(storage.sessions) as session:
            row = await session.get(RunRecord, args.run_id)
            assert row is not None and row.organization_id == config["organization_id"]
            run = row.to_resource()
        state = await RunStateStore(storage.objects).read_run(run)
        private_json(CONFIG.parent / (run.id + "-skills.json"), project(state.envelope.effective_agent_config))


if __name__ == "__main__":
    asyncio.run(main())
