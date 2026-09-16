from enum import StrEnum


class AssistantReadinessSetupActionsItem(StrEnum):
    CONFIGURE_MODEL = "configure_model"
    CONFIGURE_PROVIDER = "configure_provider"
    CONTACT_ADMINISTRATOR = "contact_administrator"

    def __str__(self) -> str:
        return str(self.value)
