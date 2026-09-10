from enum import StrEnum


class CreateTemplateRequestPreparation(StrEnum):
    ON_RUN = "on_run"
    ON_USE = "on_use"

    def __str__(self) -> str:
        return str(self.value)
