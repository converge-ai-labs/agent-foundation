"""Service envelope around the shared compact display; no host semantic fold."""

from a13n_stream_protocol import DisplayPosition, DisplaySnapshot, Producer
from pydantic import BaseModel, ConfigDict, Field

MAX_FIELD_CHARS = 262144
MAX_ITEMS = 4096


class StreamPosition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    attempt: int = Field(ge=0)
    sequence: int = Field(ge=0)

    def __str__(self) -> str:
        return f"{self.attempt}-{self.sequence}"


class Display(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    snapshot: DisplaySnapshot
    resume_after: str | None = None

    @property
    def position(self) -> StreamPosition:
        position = self.snapshot.position
        return StreamPosition(attempt=int(position.producer.generation), sequence=position.sequence)

    @classmethod
    def empty(cls, run_id: str, *, attempt: int = 0) -> "Display":
        return cls(
            snapshot=DisplaySnapshot(
                position=DisplayPosition(
                    producer=Producer(run_id=run_id, generation=str(attempt)),
                )
            )
        )

    def for_attempt(self, attempt: int) -> DisplaySnapshot:
        """Discard provisional coverage, retaining only the selected durable baseline."""
        return self.snapshot.model_copy(
            update={
                "position": DisplayPosition(
                    producer=Producer(
                        run_id=self.snapshot.position.producer.run_id,
                        generation=str(attempt),
                    )
                ),
            },
            deep=True,
        )
