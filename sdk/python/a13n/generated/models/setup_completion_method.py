from enum import StrEnum


class SetupCompletionMethod(StrEnum):
    BROWSER_CONFIRMATION = "browser_confirmation"
    OAUTH_VERIFIER = "oauth_verifier"
    POLLING = "polling"

    def __str__(self) -> str:
        return str(self.value)
