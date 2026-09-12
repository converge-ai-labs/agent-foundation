from enum import StrEnum


class AuthorizationActionType(StrEnum):
    CHECK_CONNECTION = "check_connection"
    OPEN_URL = "open_url"
    RESTART = "restart"

    def __str__(self) -> str:
        return str(self.value)
