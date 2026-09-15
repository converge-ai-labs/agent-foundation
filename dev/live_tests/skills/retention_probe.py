"""Run real collectors on an owned lab with an explicit clock and object candidate.

No lifecycle timestamps or ownership rows are forged. The zero minimum object
age makes ownership checks observable without waiting a day for the provider.
Only this short-lived process uses the supplied collection clock.
"""

import argparse
import asyncio
from datetime import datetime, timedelta
from pathlib import Path

from a13n_service.configuration.sources import load_settings
from a13n_service.object_retention.collector import ObjectCollector
from a13n_service.skills import retention
from a13n_service.skills.models import SkillUploadRecord
from a13n_service.skills.package import skill_package_object_key
from a13n_service.storage import ObjectNotFound, open_storage, short_session
from sqlalchemy import select

from ..infrastructure.config import CONFIG, load_config
from ..infrastructure.round_two_lab import private_json
from ..infrastructure.run_faults import Faults


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("digest")
    parser.add_argument("now")
    parser.add_argument("output")
    args = parser.parse_args()
    config = load_config()
    assert config.get("run_faults", {}).get("skills"), "Requires an explicitly owned Skill lab"
    assert len(args.digest) == 64 and all(c in "0123456789abcdef" for c in args.digest)
    assert Path(args.output).name == args.output
    now = datetime.fromisoformat(args.now)
    retention.utc_now = lambda: now
    faults = Faults(CONFIG.parent / "faults", "control")
    async with open_storage(load_settings().storage_settings()) as storage:
        key = skill_package_object_key(config["organization_id"], config["workspace_id"], args.digest)
        original_delete = storage.objects.delete

        async def delete(candidate, **kwargs):
            assert candidate == key
            await faults.reach("skill.collector_claimed", digest=args.digest)
            return await original_delete(candidate, **kwargs)

        storage.objects.delete = delete
        sweep = await retention.SkillUploadRetention(storage.sessions, batch_limit=1000).scan()
        collector = ObjectCollector(
            storage.sessions,
            storage.objects,
            minimum_age=timedelta(0),
            batch_limit=1000,
            item_timeout_seconds=60,
            clock=lambda: now,
        )
        await faults.reach("skill.before_collect", digest=args.digest)
        collected = await collector.collect_unowned(key)
        try:
            await storage.objects.stat(key)
            present = True
        except ObjectNotFound:
            present = False
        async with short_session(storage.sessions) as session:
            uploads = list(
                await session.scalars(
                    select(SkillUploadRecord.id).where(SkillUploadRecord.workspace_id == config["workspace_id"])
                )
            )
        private_json(
            CONFIG.parent / args.output,
            {
                "collected": collected,
                "present": present,
                "uploads": uploads,
                "expired_receipts_removed": sweep.completed,
            },
        )


if __name__ == "__main__":
    asyncio.run(main())
