from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from pathlib import Path

import pytest
from a13n_environment_provider import (
    DirectLocalEnvironment,
    DirectLocalEnvironmentProvider,
    DirectLocalProviderConfiguration,
    DirectLocalRootConfiguration,
    Environment,
)
from a13n_harness import (
    AgentIdentityRef,
    AgentInstanceContext,
    EnvironmentAccess,
    EnvironmentMount,
    HarnessBuilder,
    RunBindings,
)
from a13n_harness.environment import EnvironmentError
from a13n_harness.environment.advanced import create_empty_environment_runtime
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, FunctionModel

pytestmark = pytest.mark.anyio


async def _stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
    del messages, info
    yield "ok"


def _executable():
    return HarnessBuilder().build(
        AgentSpec(name="environment-source-test"),
        output_type=str,
        model=FunctionModel(stream_function=_stream),
    )


def _environment(root: Path, environment_id: str, *, read_only: bool = False) -> Environment:
    provider = DirectLocalEnvironmentProvider()
    return provider.create_environment(
        configuration=DirectLocalProviderConfiguration(
            environment_id=environment_id,
            root=DirectLocalRootConfiguration(path=root, read_only=read_only),
        ),
        state=None,
    )


class _TrackingEnvironment(DirectLocalEnvironment):
    def __init__(
        self,
        root: Path,
        environment_id: str,
        *,
        lifecycle_events: list[str] | None = None,
        fail_entry: bool = False,
    ) -> None:
        super().__init__(
            DirectLocalProviderConfiguration(
                environment_id=environment_id,
                root=DirectLocalRootConfiguration(path=root),
            )
        )
        self.entry: tuple[str, str, str, str, dict[str, str]] | None = None
        self.close_calls = 0
        self._lifecycle_events = lifecycle_events
        self._fail_entry = fail_entry

    async def _enter(
        self,
        *,
        thread_id: str,
        run_id: str,
        agent_instance_id: str,
        mount_id: str,
        host_refs: Mapping[str, str],
    ) -> None:
        self.entry = (thread_id, run_id, agent_instance_id, mount_id, dict(host_refs))
        if self._lifecycle_events is not None:
            self._lifecycle_events.append(f"enter:{self.environment_id}")
        if self._fail_entry:
            raise RuntimeError("entry failed")
        await super()._enter(
            thread_id=thread_id,
            run_id=run_id,
            agent_instance_id=agent_instance_id,
            mount_id=mount_id,
            host_refs=host_refs,
        )

    async def _close(self) -> None:
        self.close_calls += 1
        if self._lifecycle_events is not None:
            self._lifecycle_events.append(f"close:{self.environment_id}")
        await super()._close()


async def test_run_needs_no_environment_for_ordinary_embedded_use() -> None:
    snapshots = []

    async def prepare(context) -> str:
        snapshots.append(context.environment.snapshot)
        return "hello"

    result = await _executable().run(input_factory=prepare)

    assert result.output_or_raise() == "ok"
    assert snapshots[0].mounts == ()
    assert snapshots[0].default_mount is None


async def test_environment_input_is_entered_as_workspace_and_closed_non_destructively(
    tmp_path: Path,
) -> None:
    environment = _TrackingEnvironment(tmp_path, "workspace-environment")
    bindings = RunBindings(
        instance=AgentInstanceContext(
            identity=AgentIdentityRef(issuer="test", subject="agent"),
            agent_instance_id="agent-instance-1",
            host_refs={"attempt": "attempt-1"},
        )
    )

    async def prepare(context) -> str:
        snapshot = context.environment.snapshot
        assert snapshot.default_mount == "workspace"
        assert [mount.name for mount in snapshot.mounts] == ["workspace"]
        await context.environment.files.write_text("/workspace/value.txt", "preserved", mode="create")
        return "use environment"

    result = await _executable().run(
        input_factory=prepare,
        environment=environment,
        bindings=bindings,
    )

    assert result.output_or_raise() == "ok"
    assert (tmp_path / "value.txt").read_text() == "preserved"
    assert environment.close_calls == 1
    assert environment.entry is not None
    thread_id, run_id, agent_instance_id, mount_id, host_refs = environment.entry
    assert thread_id == result.thread_id
    assert run_id == result.run_id
    assert agent_instance_id == "agent-instance-1"
    assert mount_id.startswith("mount-")
    assert host_refs == {"attempt": "attempt-1"}


async def test_environment_mount_exposes_an_explicit_aggregate_path(tmp_path: Path) -> None:
    native_root = tmp_path.as_posix()

    async def prepare(context) -> str:
        snapshot = context.environment.snapshot
        assert snapshot.mounts[0].mount_path == native_root
        await context.environment.files.write_text(
            f"{native_root}/value.txt",
            "preserved",
            mode="create",
        )
        with pytest.raises(EnvironmentError) as legacy:
            context.environment.resolve_path("/workspace/value.txt")
        assert legacy.value.code == "environment_selection_invalid"
        return "use direct environment"

    result = await _executable().run(
        input_factory=prepare,
        environment=EnvironmentMount(
            _environment(tmp_path, "direct-environment"),
            mount_path=native_root,
        ),
    )

    assert result.output_or_raise() == "ok"
    assert (tmp_path / "value.txt").read_text() == "preserved"


async def test_multiple_environments_apply_access_and_explicit_default(tmp_path: Path) -> None:
    build_root = tmp_path / "build"
    data_root = tmp_path / "data"
    build_root.mkdir()
    data_root.mkdir()
    (data_root / "input.txt").write_text("source")

    async def prepare(context) -> str:
        snapshot = context.environment.snapshot
        assert snapshot.default_mount == "build"
        assert [mount.name for mount in snapshot.mounts] == ["build", "data"]
        await context.environment.files.write_text("/workspace/output.txt", "result", mode="create")
        source = await context.environment.files.read_text("/environment/data/input.txt")
        assert source.text == "source"
        with pytest.raises(EnvironmentError) as exc_info:
            await context.environment.files.write_text(
                "/environment/data/denied.txt",
                "denied",
                mode="create",
            )
        assert exc_info.value.code == "environment_denied"
        return "mixed"

    result = await _executable().run(
        input_factory=prepare,
        environments={
            "build": _environment(build_root, "build"),
            "data": EnvironmentMount(
                _environment(data_root, "data"),
                access=EnvironmentAccess.READ_ONLY,
            ),
        },
        default_environment="build",
    )

    assert result.output_or_raise() == "ok"
    assert (build_root / "output.txt").read_text() == "result"
    assert not (data_root / "denied.txt").exists()


async def test_multiple_environments_do_not_infer_default_from_mapping_order(tmp_path: Path) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()

    async def prepare(context) -> str:
        assert context.environment.snapshot.default_mount is None
        with pytest.raises(EnvironmentError) as exc_info:
            context.environment.resolve_path("/workspace/value.txt")
        assert exc_info.value.code == "environment_selection_invalid"
        resolved = context.environment.resolve_path("/environment/second/value.txt")
        assert resolved.mount_id
        assert resolved.path == "/value.txt"
        return "no default"

    result = await _executable().run(
        input_factory=prepare,
        environments={
            "first": _environment(first_root, "first"),
            "second": _environment(second_root, "second"),
        },
    )
    assert result.output_or_raise() == "ok"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"environment": object(), "environments": {"other": object()}},
        {"environment": object(), "default_environment": "other"},
        {"environments": {}},
        {"environments": {"one": object()}},
        {"environments": {"one": object()}, "default_environment": "missing"},
    ],
)
def test_environment_source_validation_fails_before_stream_entry(kwargs: dict[str, object]) -> None:
    with pytest.raises(EnvironmentError) as exc_info:
        _executable().stream("hello", **kwargs)  # type: ignore[arg-type]
    assert exc_info.value.code == "environment_request_invalid"


def test_one_environment_instance_cannot_be_mounted_twice(tmp_path: Path) -> None:
    environment = _environment(tmp_path, "duplicate")

    with pytest.raises(EnvironmentError) as exc_info:
        _executable().stream(
            "hello",
            environments={"one": environment, "two": environment},
        )

    assert exc_info.value.code == "environment_request_invalid"


@pytest.mark.parametrize(
    "mount_path",
    ["relative", "/with/../parent", "/double//slash", "/trailing/", "C:\\native\\path"],
)
def test_environment_mount_rejects_noncanonical_mount_path(
    tmp_path: Path,
    mount_path: str,
) -> None:
    with pytest.raises(ValueError, match="mount_path"):
        EnvironmentMount(
            _environment(tmp_path, "invalid-mount-path"),
            mount_path=mount_path,
        )


@pytest.mark.parametrize("working_directory", ["relative", "/with/../parent", "/double//slash", "/trailing/"])
def test_environment_mount_rejects_noncanonical_working_directory(
    tmp_path: Path,
    working_directory: str,
) -> None:
    with pytest.raises(ValueError, match="canonical absolute path"):
        EnvironmentMount(
            _environment(tmp_path, "invalid-working-directory"),
            working_directory=working_directory,
        )


def test_high_level_sources_conflict_with_advanced_run_binding(tmp_path: Path) -> None:
    bindings = RunBindings.embedded(environment=create_empty_environment_runtime())

    with pytest.raises(EnvironmentError) as exc_info:
        _executable().stream(
            "hello",
            environment=_environment(tmp_path, "conflict"),
            bindings=bindings,
        )

    assert exc_info.value.code == "environment_request_invalid"


async def test_environment_adapter_is_single_use(tmp_path: Path) -> None:
    environment = _environment(tmp_path, "single-use")
    first = await _executable().run("first", environment=environment)
    assert first.output_or_raise() == "ok"

    with pytest.raises(RuntimeError, match="entered exactly once"):
        await _executable().run("second", environment=environment)


async def test_entered_environments_close_in_reverse_order_when_later_entry_fails(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    roots = [tmp_path / name for name in ("first", "second", "failed")]
    for root in roots:
        root.mkdir()
    first = _TrackingEnvironment(roots[0], "first", lifecycle_events=events)
    second = _TrackingEnvironment(roots[1], "second", lifecycle_events=events)
    failed = _TrackingEnvironment(roots[2], "failed", lifecycle_events=events, fail_entry=True)

    with pytest.raises(RuntimeError, match="entry failed"):
        async with _executable().stream(
            "hello",
            environments={"first": first, "second": second, "failed": failed},
        ):
            pass

    assert events == [
        "enter:first",
        "enter:second",
        "enter:failed",
        "close:failed",
        "close:second",
        "close:first",
    ]
