from enum import StrEnum


class ModelCatalogMatchSource(StrEnum):
    AMBIGUOUS = "ambiguous"
    EXACT = "exact"
    EXPLICIT = "explicit"
    NAME_TOKENS = "name_tokens"
    NONE = "none"
    NORMALIZED = "normalized"

    def __str__(self) -> str:
        return str(self.value)
