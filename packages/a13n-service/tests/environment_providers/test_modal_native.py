"""Modal's real async SDK against fixture-owned control and command-router gRPC."""

import asyncio
import functools
import shutil
import sys
from unittest.mock import AsyncMock

import pytest
from a13n_harness.environment.errors import EnvironmentProviderError
from a13n_harness.environment.models import EnvironmentState
from a13n_harness.providers.catalog import ProviderCatalog
from a13n_service.providers.environments import BUILT_IN_ENVIRONMENT_PROVIDERS
from a13n_service.providers.environments.modal.management import ModalManagement
from google.protobuf.empty_pb2 import Empty
from google.protobuf.message_factory import GetMessageClass
from grpclib import GRPCError, Status
from grpclib.const import Cardinality, Handler
from grpclib.server import Server
from modal_proto import api_pb2 as api
from modal_proto import task_command_router_pb2 as router

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="Fixture executes POSIX guest helpers on the Host")


class ModalCloud:
    def __init__(self, root):
        self.root = root
        self.url = ""
        self.sandboxes = {}
        self.images = {}
        self.processes = {}
        self.names = {}
        self.created = 0
        self.calls = []
        self.definitions = []

    def __mapping__(self):
        result = {}
        for module in [api, router]:
            for service in module.DESCRIPTOR.services_by_name.values():
                for method in service.methods:
                    cardinality = Cardinality.UNARY_STREAM if method.server_streaming else Cardinality.UNARY_UNARY
                    result[f"/{service.full_name}/{method.name}"] = Handler(
                        functools.partial(self.handle, method.name),
                        cardinality,
                        GetMessageClass(method.input_type),
                        GetMessageClass(method.output_type),
                    )
        return result

    async def handle(self, name, stream):
        request = await stream.recv_message()
        self.calls.append(name)
        if name == "ClientHello":
            response = api.ClientHelloResponse()
        elif name == "AppGetOrCreate":
            response = api.AppGetOrCreateResponse(app_id="ap-fixture")
        elif name == "EnvironmentGetOrCreate":
            response = api.EnvironmentGetOrCreateResponse(
                environment_id="en-fixture",
                metadata=api.EnvironmentMetadata(
                    name="main", settings=api.EnvironmentSettings(image_builder_version="2025.06")
                ),
            )
        elif name == "SandboxGetFromName":
            sandbox_id = self.names.get(request.sandbox_name)
            if sandbox_id is None or self.sandboxes[sandbox_id]["stopped"]:
                raise GRPCError(Status.NOT_FOUND)
            response = api.SandboxGetFromNameResponse(
                sandbox_id=sandbox_id, metadata=api.SandboxHandleMetadata(app_id="ap-fixture")
            )
        elif name == "ImageGetOrCreate":
            image_id = f"im-base{len(self.images)}"
            self.images[image_id] = None
            response = api.ImageGetOrCreateResponse(
                image_id=image_id, metadata=api.ImageMetadata(image_builder_version=request.builder_version)
            )
        elif name == "ImageFromId":
            response = api.ImageFromIdResponse(
                image_id=request.image_id, metadata=api.ImageMetadata(image_builder_version="2025.06")
            )
        elif name == "ImageJoinStreaming":
            response = api.ImageJoinStreamingResponse(
                result=api.GenericResult(status=api.GenericResult.GENERIC_STATUS_SUCCESS),
                metadata=api.ImageMetadata(image_builder_version="2025.06"),
            )
        elif name == "SecretGetOrCreate":
            response = api.SecretGetOrCreateResponse(secret_id="st-fixture")
        elif name == "SandboxCreate":
            self.definitions.append(request.definition)
            self.created += 1
            sandbox_id = f"sb-nGEijt9WbBMlGrsPH9FOa{self.created}"
            self.names[request.definition.name] = sandbox_id
            self.sandboxes[sandbox_id] = {"stopped": False, "tags": {t.tag_name: t.tag_value for t in request.tags}}
            if snapshot := self.images.get(request.definition.image_id):
                shutil.rmtree(self.root)
                shutil.copytree(snapshot, self.root)
            response = api.SandboxCreateResponse(
                sandbox_id=sandbox_id, metadata=api.SandboxHandleMetadata(app_id="ap-fixture")
            )
        elif name == "SandboxWait":
            target = self.sandboxes.get(request.sandbox_id)
            if target is None:
                raise GRPCError(Status.NOT_FOUND)
            status = api.GenericResult.GENERIC_STATUS_TERMINATED if target["stopped"] else 0
            result = api.GenericResult(status=status, exitcode=-1 if status else 0)
            response = api.SandboxWaitResponse(
                result=result, metadata=api.SandboxHandleMetadata(app_id="ap-fixture", result=result)
            )
        elif name == "SandboxTagsGet":
            response = api.SandboxTagsGetResponse(
                tags=[
                    api.SandboxTag(tag_name=k, tag_value=v)
                    for k, v in self.sandboxes[request.sandbox_id]["tags"].items()
                ]
            )
        elif name == "SandboxTagsSet":
            self.sandboxes[request.sandbox_id]["tags"].update({t.tag_name: t.tag_value for t in request.tags})
            response = Empty()
        elif name == "SandboxGetTaskId":
            response = api.SandboxGetTaskIdResponse(task_id="ta-" + request.sandbox_id)
        elif name == "TaskGetCommandRouterAccess":
            response = api.TaskGetCommandRouterAccessResponse(url=self.url, jwt="fixture")
        elif name == "TaskExecStart":
            proc = await asyncio.create_subprocess_exec(
                *request.command_args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            self.processes[request.exec_id] = proc
            response = router.TaskExecStartResponse()
        elif name == "TaskExecStdioRead":
            proc = self.processes[request.exec_id]
            output = (
                proc.stdout if request.file_descriptor == router.TASK_EXEC_STDIO_FILE_DESCRIPTOR_STDOUT else proc.stderr
            )
            while chunk := await output.read(65536):
                await stream.send_message(router.TaskExecStdioReadResponse(data=chunk))
            return
        elif name == "TaskExecWait":
            proc = self.processes[request.exec_id]
            await proc.wait()
            response = router.TaskExecWaitResponse(code=proc.returncode)
        elif name == "TaskSnapshotFilesystem":
            assert request.ttl_seconds == -1
            image_id = f"im-snapshot{len(self.images)}"
            location = self.root.parent / image_id
            shutil.copytree(self.root, location)
            self.images[image_id] = location
            response = router.TaskSnapshotFilesystemResponse(image_id=image_id)
        elif name == "SandboxTerminate":
            self.sandboxes[request.sandbox_id]["stopped"] = True
            shutil.rmtree(self.root)
            self.root.mkdir()
            response = api.SandboxTerminateResponse()
        elif name == "ImageDelete":
            location = self.images.pop(request.image_id, None)
            if location:
                shutil.rmtree(location)
            response = Empty()
        else:
            raise GRPCError(Status.UNIMPLEMENTED, name)
        await stream.send_message(response)


@pytest.mark.parametrize("desktop", [False, True])
def test_modal_real_sdk_snapshot_resume(tmp_path, monkeypatch, desktop):
    async def scenario():
        root = tmp_path / "guest"
        root.mkdir()
        cloud = ModalCloud(root)
        server = Server([cloud])
        await server.start("127.0.0.1", 0)
        port = server._server.sockets[0].getsockname()[1]
        cloud.url = f"http://127.0.0.1:{port}"
        monkeypatch.setenv("MODAL_SERVER_URL", cloud.url)
        monkeypatch.setenv("MODAL_SANDBOX_V2", "false")
        ready = AsyncMock()
        monkeypatch.setattr(ModalManagement, "_check_envd", ready)
        access = AsyncMock()
        monkeypatch.setattr(ModalManagement, "desktop_access", access)
        definition = ProviderCatalog(BUILT_IN_ENVIRONMENT_PROVIDERS)["modal"]
        config = definition.validate_environment(
            {"image": "im-envd", "request_timeout_seconds": 15, "desktop": desktop, "desktop_assistance": desktop}
        )
        provider = await definition.open_provider(
            configuration={"workspace": "fixture", "app_name": "fixture"},
            credential={"token_id": "fixture", "token_secret": "fixture"},
        )

        async def manage(operation, state=None):
            return await getattr(provider, operation)(
                config, environment_id="env-fixture", state=state, operation_id="op-test"
            )

        def connector(state):
            return definition.execution.execution_connector(
                config,
                configuration={"workspace": "fixture", "app_name": "fixture"},
                credential={"token_id": "fixture", "token_secret": "fixture"},
                environment_id="env-fixture",
                state=state,
            )

        try:
            state = await manage("create")
            assert ready.await_count == 1
            launch = cloud.definitions[0]
            assert launch.entrypoint_args[-1].endswith("a13n-sandbox-desktop" if desktop else "a13n-envd")
            assert access.await_count == int(desktop)
            assert launch.workdir == "/workspace"
            assert [port.port for port in launch.open_ports.ports] == ([8888, 6081] if desktop else [8888])
            assert not any(port.unencrypted for port in launch.open_ports.ports)
            (root / "data").write_text("saved in native snapshot")
            running = EnvironmentState.model_validate_json(state.model_dump_json())
            state = await manage("stop", running)
            assert state.state["snapshot_id"]
            assert not (root / "data").exists()
            assert (await provider.inspect(config, environment_id="env-fixture", state=state)).status == "stopped"
            before = list(cloud.calls)
            with pytest.raises(EnvironmentProviderError):
                await connector(state).open()
            assert not {"SandboxCreate", "TaskExecStart", "ImageDelete", "SandboxTagsSet"}.intersection(
                cloud.calls[len(before) :]
            )
            # Retrying a stop from its prior reference adopts the retained snapshot.
            assert (await manage("stop", running)).state == state.state
            state = await manage("start", state)
            assert (root / "data").read_text() == "saved in native snapshot"
            assert ready.await_count == 2
            assert cloud.created == 2
            assert "TaskSnapshotFilesystem" in cloud.calls
            assert "ImageDelete" in cloud.calls
            await provider.close()
            provider = await definition.open_provider(
                configuration={"workspace": "fixture", "app_name": "fixture"},
                credential={"token_id": "fixture", "token_secret": "fixture"},
            )
            await manage("destroy", state)
            before = cloud.created
            for operation in ("create", "start"):
                with pytest.raises(EnvironmentProviderError):
                    await manage(operation, state)
            assert cloud.created == before
        finally:
            await provider.close()
            server.close()
            await server.wait_closed()

    asyncio.run(scenario())
