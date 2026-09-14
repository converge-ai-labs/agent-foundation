"""Lab-owned, process-shared fault barriers with durable, bounded evidence."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import anyio
from a13n_service.storage import ObjectConflict, ObjectStoreUnavailable
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)


class FaultRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    point: str = Field(pattern=r"^[a-z_]+(?:\.[a-z_]+)+$")
    role: Literal["control", "worker"] = "worker"
    match: dict[str, str | int | bool] = Field(default_factory=dict)
    action: Literal["pause", "unavailable", "conflict", "observe"] = "pause"
    times: int = Field(default=1, ge=1, le=100)


@dataclass(frozen=True)
class FaultTicket:
    path: Path
    rule: FaultRule
    evidence: dict

    async def apply(self):
        logger.info("live fault reached: %s", self.evidence)
        try:
            if self.rule.action == "pause":
                with anyio.fail_after(90):
                    while not await anyio.Path(self.path / "release").exists():
                        await anyio.sleep(0.025)
            elif self.rule.action == "unavailable":
                raise ObjectStoreUnavailable("Injected lab-owned dependency failure")
            elif self.rule.action == "conflict":
                raise ObjectConflict("Injected lab-owned conditional-write conflict")
        finally:
            # This tiny lab-local observation must not introduce an await: raw
            # repeated Task.cancel() can interrupt even an AnyIO shielded write.
            (self.path / f"finished-{self.evidence['hit']}.json").write_text(
                json.dumps({**self.evidence, "finished_at": datetime.now(UTC).isoformat()})
            )


class Faults:
    """Only rules beneath this disposable lab can affect its service processes."""

    def __init__(self, root: Path, role: str):
        self.root, self.role = root, role

    def _take(self, point, facts):
        for path in sorted(self.root.glob("*/rule.json")):
            rule = FaultRule.model_validate_json(path.read_text())
            if rule.point != point or rule.role != self.role:
                continue
            if any(facts.get(key) != value for key, value in rule.match.items()):
                continue
            for hit in range(1, rule.times + 1):
                try:
                    descriptor = os.open(path.parent / f"hit-{hit}.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                except FileExistsError:
                    continue
                evidence = {"point": point, "role": self.role, "pid": os.getpid(), "hit": hit, **facts}
                evidence["reached_at"] = datetime.now(UTC).isoformat()
                with os.fdopen(descriptor, "w") as output:
                    json.dump(evidence, output)
                return FaultTicket(path.parent, rule, evidence)
        return None

    async def reach(self, point, **facts):
        ticket = await self.take(point, **facts)
        if ticket is not None:
            await ticket.apply()

    async def take(self, point, **facts):
        return await anyio.to_thread.run_sync(lambda: self._take(point, facts))


def arm(root: Path, name: str, **rule):
    """Publish a complete rule atomically; never overwrite existing evidence."""
    if not name or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for character in name):
        raise ValueError("Fault names must be local single path components")
    value = FaultRule.model_validate(rule)
    path = root / name
    path.mkdir(parents=True, mode=0o700)
    temporary = path / "rule.tmp"
    temporary.write_text(value.model_dump_json())
    temporary.chmod(0o600)
    temporary.replace(path / "rule.json")
    return path
