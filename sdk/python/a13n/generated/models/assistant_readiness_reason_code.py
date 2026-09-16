from enum import StrEnum


class AssistantReadinessReasonCode(StrEnum):
    COMPATIBLE_MODEL_REQUIRED = "compatible_model_required"
    MODEL_ACCESS_DENIED = "model_access_denied"
    MODEL_SETUP_REQUIRED = "model_setup_required"
    PROVIDER_SETUP_REQUIRED = "provider_setup_required"
    READY = "ready"

    def __str__(self) -> str:
        return str(self.value)
