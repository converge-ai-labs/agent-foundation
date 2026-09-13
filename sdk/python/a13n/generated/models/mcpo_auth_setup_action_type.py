from enum import StrEnum


class MCPOAuthSetupActionType(StrEnum):
    AUTHENTICATE_CLIENT_CREDENTIALS = "authenticate_client_credentials"
    CHECK_CONNECTION = "check_connection"
    COMPLETED = "completed"
    CONFIGURE_OAUTH_CLIENT = "configure_oauth_client"
    START_AUTHORIZATION = "start_authorization"

    def __str__(self) -> str:
        return str(self.value)
