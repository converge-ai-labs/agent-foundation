"""A trusted Connector with structured setup and plugin-owned periodic matching."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import ClassVar

from a13n_harness_ui.app import HarnessUiIntegrations, open_harness_ui_app
from a13n_harness_ui.connectors import (
    ConnectorConfiguration,
    ConnectorInput,
    ConnectorRule,
    Context,
    Controller,
    Declaration,
    Definition,
    EventDeclaration,
    SetupAction,
    SetupFlow,
    StateChange,
    Status,
    Step,
)
from a13n_harness_ui.settings_loader import load_harness_ui_settings
from a13n_harness_ui.webui import run
from anyio import sleep
from pydantic import BaseModel, ConfigDict, Field, JsonValue


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(default="Demo", min_length=1, max_length=80)
    interval_seconds: int = Field(default=60, ge=10, le=3600)


class Conditions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    every: int = Field(default=2, ge=1, le=100)


def matches(rules: tuple[ConnectorRule, ...], sequence: int) -> bool:
    return any(
        rule.enabled and rule.event == "tick" and sequence % Conditions.model_validate(rule.conditions).every == 0
        for rule in rules
    )


class Periodic(Definition):
    configuration_model = Settings
    condition_models: ClassVar[dict[str, type[BaseModel] | None]] = {"tick": Conditions}

    def declaration(self) -> Declaration:
        return Declaration(
            key="example-periodic",
            name="Periodic example",
            description="Generate local demo ticks with plugin-owned matching.",
            configuration_schema=Settings.model_json_schema(),
            events=(EventDeclaration(key="tick", name="Tick", conditions_schema=Conditions.model_json_schema()),),
            bot_delivery=True,
        )

    @asynccontextmanager
    async def open(self, context: Context) -> AsyncIterator[Controller]:
        yield PeriodicController(context)


class PeriodicController(Controller):
    async def status(self) -> Status:
        current = await self.context.configuration()
        return Status(
            state="ready" if current.enabled else "disabled",
            summary="Local demo producer; no external account required.",
        )

    async def begin_setup(self, intent: str) -> SetupFlow:
        return Setup(self.context)

    async def run(self, configuration: ConnectorConfiguration) -> None:
        settings = Settings.model_validate(configuration.settings)
        while True:
            await sleep(settings.interval_seconds)
            previous = await self.context.state("cursor")
            sequence = 1
            if previous is not None:
                cursor = previous.value
                if not isinstance(cursor, dict) or cursor.get("codec") != 1:
                    raise ValueError("Unsupported demo cursor codec")
                number = cursor.get("sequence")
                if not isinstance(number, int):
                    raise ValueError("Invalid demo cursor")
                sequence = number + 1
            inputs = ()
            if configuration.deliver_to_bot and matches(configuration.rules, sequence):
                inputs = (
                    ConnectorInput(
                        connector_id=configuration.connector_id,
                        event_type="tick",
                        source_event_id=f"tick-{sequence}",
                        text=f"{settings.label}: demo tick {sequence}.",
                    ),
                )
            # Cursor advancement and retained input share one short transaction.
            # A failed commit leaves the previous cursor intact.
            await self.context.commit(
                configuration_version=configuration.version,
                state_changes=(
                    StateChange(
                        key="cursor",
                        expected_version=previous.version if previous is not None else None,
                        value={"codec": 1, "sequence": sequence},
                    ),
                ),
                bot_inputs=inputs,
            )


class Setup(SetupFlow):
    def __init__(self, context: Context) -> None:
        self.context = context
        self.current: ConnectorConfiguration | None = None

    async def read(self) -> Step:
        if self.current is None:
            self.current = await self.context.configuration()
        return Step(
            key="configure",
            title="Configure periodic example",
            form=Settings.model_json_schema(),
            values=Settings.model_validate(self.current.settings).model_dump(mode="json"),
            actions=(SetupAction(id="save", label="Save connection"),),
        )

    async def act(self, action: str, values: dict[str, JsonValue]) -> Step:
        if action != "save" or self.current is None:
            raise ValueError("Read setup before saving")
        settings = Settings.model_validate(values).model_dump(mode="json")
        await self.context.configure(
            self.current.model_copy(update={"settings": settings}), expected_version=self.current.version
        )
        return Step(key="saved", title="Connection saved", state="completed")


async def serve(config: Path, port: int) -> None:
    source = await load_harness_ui_settings(config)
    if source.candidate_error is not None:
        raise source.candidate_error
    await run(
        lambda: open_harness_ui_app(
            source.settings,
            configuration_path=source.path,
            host_mode="webui",
            integrations=HarnessUiIntegrations(connectors=(Periodic(),)),
        ),
        port=port,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    asyncio.run(serve(args.config, args.port))
