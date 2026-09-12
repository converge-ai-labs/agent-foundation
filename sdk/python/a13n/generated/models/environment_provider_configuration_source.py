from enum import StrEnum


class EnvironmentProviderConfigurationSource(StrEnum):
    DEPLOYMENT = "deployment"
    USER = "user"

    def __str__(self) -> str:
        return str(self.value)
