"""Trusted conversation selection retained across execution and recovery."""

from pydantic import BaseModel, ConfigDict, Field, model_validator

from a13n_service.ids import ObjectId


class BotMemoryBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    account_id: ObjectId
    external_conversation_id: str = Field(min_length=1, max_length=512)
    provider_id: ObjectId | None = None
    scope_id: ObjectId | None = None
    scope_version: int | None = Field(default=None, ge=1)
    use_memory: bool = False
    save_on_request: bool = False
    auto_organize: bool = False

    @model_validator(mode="after")
    def require_scope(self) -> "BotMemoryBinding":
        if (self.use_memory or self.save_on_request) and (
            self.provider_id is None or self.scope_id is None or self.scope_version is None
        ):
            raise ValueError("Enabled Bot memory requires a retained conversation scope")
        return self
