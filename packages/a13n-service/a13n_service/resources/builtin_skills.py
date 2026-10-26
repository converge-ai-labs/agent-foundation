"""Release-owned skill definitions shared by resource APIs and execution."""

from dataclasses import dataclass
from typing import Literal

from a13n_harness.environment.archives import FileArchive
from pydantic import BaseModel

type BuiltinSkillName = Literal["platform-operations"]


class BuiltinSkill(BaseModel):
    name: BuiltinSkillName
    description: str


class BuiltinSkillCatalog(BaseModel):
    items: list[BuiltinSkill]


@dataclass(frozen=True, slots=True)
class BuiltinPackage:
    definition: BuiltinSkill
    digest: str
    archive: FileArchive
