from enum import StrEnum


class MCPOAuthClientConfigurationTokenEndpointAuthMethod(StrEnum):
    CLIENT_SECRET_BASIC = "client_secret_basic"
    CLIENT_SECRET_POST = "client_secret_post"
    NONE = "none"

    def __str__(self) -> str:
        return str(self.value)
