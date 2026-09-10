from enum import StrEnum


class EnvironmentTemplateRevisionPreparation(StrEnum):
    ON_RUN = "on_run"
    ON_USE = "on_use"

    def __str__(self) -> str:
        return str(self.value)
