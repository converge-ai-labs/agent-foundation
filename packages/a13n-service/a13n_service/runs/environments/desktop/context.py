"""Read-only per-request projection of native Computer admission, not provider readiness."""

from collections.abc import Mapping

from a13n_harness.environment.models import EnvironmentState
from pydantic import JsonValue
from sqlalchemy import select

from a13n_service.infra.db import Storage, short_session
from a13n_service.runs.environments.desktop.identity import target_key
from a13n_service.runs.environments.desktop.tables import DesktopControlRow


async def project(storage: Storage, states: Mapping[str, EnvironmentState]) -> list[dict[str, JsonValue]]:
    targets = {name: key for name, state in states.items() if (key := target_key(state)) is not None}
    if not targets:
        return []
    # Select only model-visible control facts. No credentials, row creation, locks,
    # provider calls or notification registration belong to context projection.
    async with short_session(storage) as session:
        rows = {
            row.target_key: row
            for row in await session.execute(
                select(
                    DesktopControlRow.target_key,
                    DesktopControlRow.control,
                    DesktopControlRow.generation,
                    DesktopControlRow.episode,
                    DesktopControlRow.failure,
                ).where(DesktopControlRow.target_key.in_(set(targets.values())))
            )
        }
    result: list[dict[str, JsonValue]] = []
    for name, key in targets.items():
        row = rows.get(key)
        lost = row is not None and row.failure == "environment_lost"
        blocked = row is not None and row.control != "automation"
        item: dict[str, JsonValue] = {
            "alias": name,
            "admission": "unavailable" if lost else "human_control" if blocked else "open",
            "hint": (
                "The native desktop was lost. Computer use is unavailable."
                if lost
                else "Computer calls are blocked by human control. Use computer_notify to register for handback, "
                "then continue other work or finish without polling."
                if blocked
                else "Computer admission is open; mount permissions and provider readiness still apply."
            ),
        }
        if row is not None:
            item.update(control=row.control, generation=row.generation, episode=row.episode)
            if row.episode > 0 and not lost and not blocked:
                item["hint"] = (
                    "Computer admission is open. After handback, obtain a fresh observation and let the model "
                    "receive it before input. Mount permissions and provider readiness still apply."
                )
        result.append(item)
    return result
