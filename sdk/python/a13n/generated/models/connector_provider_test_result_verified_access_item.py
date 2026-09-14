from enum import StrEnum


class ConnectorProviderTestResultVerifiedAccessItem(StrEnum):
    ACCOUNT_READ = "account_read"
    CATALOG_READ = "catalog_read"

    def __str__(self) -> str:
        return str(self.value)
