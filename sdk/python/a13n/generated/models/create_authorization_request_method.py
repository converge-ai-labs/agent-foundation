from enum import StrEnum


class CreateAuthorizationRequestMethod(StrEnum):
    BROWSER = "browser"
    CLIENT_CREDENTIALS = "client_credentials"
    CREDENTIALS = "credentials"

    def __str__(self) -> str:
        return str(self.value)
