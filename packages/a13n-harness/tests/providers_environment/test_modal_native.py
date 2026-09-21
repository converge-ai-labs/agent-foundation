"""Modal's real async SDK against fixture-owned control and command-router gRPC."""

import asyncio
import functools
import shutil
import sys

import pytest
from a13n_harness.providers.catalog import ProviderCatalog
from a13n_harness.providers.environment.builtins import select_builtin_environment_providers
from a13n_harness.providers.environment.models import EnvironmentState
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
        elif name == "SandboxCreate":
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


def test_modal_real_sdk_snapshot_resume(tmp_path, monkeypatch):
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
        provider = ProviderCatalog(select_builtin_environment_providers(["modal"])).require("modal")
        config = provider.validate_environment(
            {"root": str(root), "python": sys.executable, "request_timeout_seconds": 15}
        )
        runtime = await provider.runtime_factory(
            configuration=provider.configuration_model(workspace="fixture", app_name="fixture"),
            credential=provider.credential_model(token_id="fixture", token_secret="fixture"),
        )

        def create(state=None):
            return provider.construct(
                operation_id="op-test",
                allow_create=True,
                configuration=config,
                environment_id="env-fixture",
                state=state,
                runtime=runtime,
            )

        env = create()
        try:
            await env.prepare()
            await env.operations.files.write_text("/data", "saved in native snapshot", mode="upsert")
            state = EnvironmentState.model_validate_json(env.dump_state().model_dump_json())
            await env.close()
            env = create(state)
            await env.stop()
            state = EnvironmentState.model_validate_json(env.dump_state().model_dump_json())
            assert state.state["snapshot_id"]
            assert not (root / "data").exists()
            assert await env.reconcile() == "stopped"
            await env.close()
            env = create(state)
            await env.prepare()
            assert (await env.operations.files.read_text("/data")).text == "saved in native snapshot"
            assert cloud.created == 2
            assert "TaskSnapshotFilesystem" in cloud.calls
            assert "ImageDelete" in cloud.calls
            state = env.dump_state()
            await env.close()
            env = create(state)
            await env.destroy()
        finally:
            await env.close()
            server.close()
            await server.wait_closed()

    asyncio.run(scenario())
