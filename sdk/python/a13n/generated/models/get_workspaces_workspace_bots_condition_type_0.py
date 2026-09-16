from enum import StrEnum


class GetWorkspacesWorkspaceBotsConditionType0(StrEnum):
    CHECK_FAILED = "check_failed"
    DISABLED = "disabled"
    NEEDS_VERIFICATION = "needs_verification"
    RECEIVING = "receiving"
    RECEPTION_OFF = "reception_off"

    def __str__(self) -> str:
        return str(self.value)
