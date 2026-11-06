"""Independent App process used by native Shell crash-recovery tests."""

import asyncio
import json
import sys
from pathlib import Path

from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.environment_bindings import EnvironmentBindingSelection, EnvironmentSelectionPatch
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings
from a13n_harness_ui.storage.tool_executions import ToolExecutionRepository
from pydantic_ai.models.function import DeltaToolCall, FunctionModel


async def main() -> None:
    path, data, ready, device, directory, command, lose_response = sys.argv[1:]
    requested = False

    async def model(messages, info):
        nonlocal requested
        if not requested:
            requested = True
            yield {
                0: DeltaToolCall(
                    name="shell_exec",
                    json_args=json.dumps({"command": command, "alias": "build"}),
                    tool_call_id="shell-original",
                )
            }
        else:
            yield "Job admitted."

    async def resolve(self, context, model_id):
        return FunctionModel(stream_function=model)

    HarnessUiModelResolver.__call__ = resolve
    save = ToolExecutionRepository.save

    def publish(execution):
        Path(ready).write_text(
            json.dumps(
                {
                    "thread_id": execution.record.thread_id,
                    "execution_id": execution.record.execution_id,
                    "process": execution.shell.process.model_dump(mode="json"),
                }
            )
        )

    async def save_checkpoint(self, execution):
        if (
            execution.shell is not None
            and execution.shell.process.identity is not None
            and not execution.record.terminal
        ):
            if lose_response == "true":
                # Native admission succeeded, but only start correlation is durable.
                publish(execution)
                await asyncio.Event().wait()
            await save(self, execution)
            publish(execution)
        else:
            await save(self, execution)

    ToolExecutionRepository.save = save_checkpoint
    settings = HarnessUiSettings(storage=StorageSettings(data_root=Path(data)), shutdown_timeout_seconds=1)
    async with open_harness_ui_app(settings, configuration_path=Path(path)) as app:
        async with asyncio.timeout(15):
            while not (await app.device_info(device)).available:
                await asyncio.sleep(0.02)
        thread = await app.create_thread()
        await app.submit_thread(
            thread_id=thread.thread_id,
            prompt="Start the job.",
            environment=EnvironmentSelectionPatch(
                default_environment="build",
                environment_bindings=(
                    EnvironmentBindingSelection(device_id=device, alias="build", working_directory=directory),
                ),
            ),
        )
        await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
