from enum import StrEnum


class MCPOAuthClientInputGrantType(StrEnum):
    AUTHORIZATION_CODE = "authorization_code"
    CLIENT_CREDENTIALS = "client_credentials"

    def __str__(self) -> str:
        return str(self.value)
