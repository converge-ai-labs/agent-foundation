from enum import StrEnum


class CreateTemplateRevisionRequestPreparation(StrEnum):
    ON_RUN = "on_run"
    ON_USE = "on_use"

    def __str__(self) -> str:
        return str(self.value)
