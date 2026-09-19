"""Hosted definition reconstruction exposes permitted Environment tools in every Agent."""

import json
import shutil
from pathlib import Path

import pytest
from a13n_environment import (
    FILE_ACTIONS,
    FILE_READ_ACTIONS,
    DirectLocalEnvironmentProvider,
    DirectLocalProviderConfiguration,
    DirectLocalRootConfiguration,
    DirectLocalShellProfile,
    EnvironmentAction,
    EnvironmentPermissionSet,
)
from a13n_harness import EnvironmentMount, HarnessBuilder, RunBindings
from pydantic_ai.messages import ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from .test_reconstruction import CHILD_REVISION_ID, _config, _edge, _effective, _reconstruct, _revision

READ_TOOLS = {"view", "ls", "glob", "grep"}
FILE_TOOLS = READ_TOOLS | {"write", "edit", "multi_edit", "mkdir", "move", "copy", "delete"}
SHELL_TOOLS = (FILE_TOOLS - {"move", "copy", "delete"}) | {
    "shell_exec",
    "shell_info",
    "shell_wait",
    "shell_input",
    "shell_signal",
}


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("ceiling", "shell", "prepared", "expected_tools"),
    [
        pytest.param(None, False, False, set(), id="no-environment"),
        pytest.param(FILE_READ_ACTIONS, True, False, READ_TOOLS, id="read-only-ceiling"),
        pytest.param(FILE_ACTIONS, True, False, FILE_TOOLS, id="files-only-ceiling"),
        pytest.param(frozenset(EnvironmentAction), False, False, FILE_TOOLS, id="provider-without-shell"),
        pytest.param(frozenset(EnvironmentAction), True, False, SHELL_TOOLS, id="lazy-shell"),
        pytest.param(frozenset(EnvironmentAction), True, True, SHELL_TOOLS, id="prepared-shell"),
    ],
)
async def test_root_and_inline_child_receive_and_use_environment_tools(
    tmp_path, ceiling, shell, prepared, expected_tools
):
    environment = None
    if ceiling is not None:
        executable = shutil.which("sh") if shell else None
        if shell and executable is None:
            pytest.skip("A POSIX shell is required for this Provider configuration")
        environment = DirectLocalEnvironmentProvider().create_environment(
            configuration=DirectLocalProviderConfiguration(
                root=DirectLocalRootConfiguration(path=tmp_path),
                shell_profiles=(DirectLocalShellProfile(profile_id="default", executable=Path(executable)),)
                if executable
                else (),
            ),
            environment_id="hosted-workspace",
            state=None,
        )
        if prepared:
            await environment.prepare()
    (tmp_path / "marker.txt").write_text("hosted-environment-file", encoding="utf-8")
    definition = _reconstruct(
        _effective(_config(), subagents=(_edge("helper").model_copy(update={"usage_limits": None}),)),
        children={CHILD_REVISION_ID: _revision()},
    )
    observed = {"root": [], "child": []}
    returned = {"root": [], "child": []}

    def model_for(role):
        async def stream(messages, info):
            names = {tool.name for tool in info.function_tools}
            observed[role].append(names)
            assert names == expected_tools | ({"delegate", "resume_subagent"} if role == "root" else set())
            if role == "root" and len(observed[role]) == 1 and environment is not None:
                assert environment.availability.status == ("available" if prepared else "preparing")
            returned[role] = [
                part for message in messages for part in message.parts if isinstance(part, ToolReturnPart)
            ]
            actions = []
            if "view" in names:
                actions.append(("view", {"file_path": "/workspace/marker.txt"}))
            if "shell_exec" in names:
                actions.append(("shell_exec", {"command": "printf hosted-environment-shell"}))
            if role == "root":
                actions.append(("delegate", {"subagent": "helper", "prompt": "Inspect the shared workspace."}))
            step = len(observed[role]) - 1
            if step < len(actions):
                name, arguments = actions[step]
                yield {
                    0: DeltaToolCall(
                        name=name,
                        json_args=json.dumps(arguments),
                        tool_call_id=f"{role}-{step}",
                    )
                }
            else:
                yield f"{role} completed"

        return FunctionModel(stream_function=stream)

    async def resolve_model(context, model_id):
        return model_for("child" if context.deps.instance.parent_agent_instance_id else "root")

    result = (
        await HarnessBuilder()
        .build(definition)
        .run(
            "Inspect the workspace, then delegate the same check.",
            environment=EnvironmentMount(environment, permission_ceiling=EnvironmentPermissionSet(operations=ceiling))
            if environment is not None
            else None,
            bindings=RunBindings.embedded(model_resolver=resolve_model),
        )
    )

    assert result.output_or_raise() == "root completed"
    assert "child completed" in str([part.content for part in returned["root"] if part.tool_name == "delegate"])
    for role in ("root", "child"):
        assert observed[role]
        if "view" in expected_tools:
            assert "hosted-environment-file" in str(
                [part.content for part in returned[role] if part.tool_name == "view"]
            )
        if "shell_exec" in expected_tools:
            assert "hosted-environment-shell" in str(
                [part.content for part in returned[role] if part.tool_name == "shell_exec"]
            )
    if environment is not None:
        assert not environment.is_entered
    assert (tmp_path / "marker.txt").read_text(encoding="utf-8") == "hosted-environment-file"
