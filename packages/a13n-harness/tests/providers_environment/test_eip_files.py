"""Adapter failures remain Environment errors before native transfer dispatch."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from a13n_envd_client.eip import v1 as eip
from a13n_envd_client.errors import EIPSessionStateError
from a13n_harness.environment.models import EnvironmentError
from a13n_harness.providers.environment.eip.files import EIPFileOperator

pytestmark = pytest.mark.anyio


def operator(*, methods=(), failure=None):
    session = SimpleNamespace(
        descriptor=SimpleNamespace(
            working_directory="/",
            available_methods=methods,
        ),
        open_reader=Mock(side_effect=failure),
        open_writer=Mock(side_effect=failure),
    )
    return EIPFileOperator(session=session, environment_id="env", execution_id="workspace", generation="1"), session


@pytest.mark.parametrize("path", ["/../outside", "/a/../../outside", "/a//b", "/a/./b", "/a/"])
def test_invalid_wire_paths_become_environment_errors(path):
    files, _ = operator()
    with pytest.raises(EnvironmentError) as error:
        files.to_eip_path(path)
    assert error.value.code == "environment_request_invalid"


async def forbidden_upload():
    raise AssertionError("Rejected transfer consumed input")
    yield b"never"


async def invoke_transfer(files, action):
    if action == "read":
        return await files.read_bytes("/file")
    if action == "read-stream":
        return [chunk async for chunk in files.read_bytes_stream("/file")]
    return await files.write_bytes_stream("/file", forbidden_upload(), mode="create")


@pytest.mark.parametrize("action", ["read", "read-stream", "write"])
async def test_unadvertised_transfers_fail_before_native_dispatch(action):
    files, session = operator()
    with pytest.raises(EnvironmentError) as error:
        await invoke_transfer(files, action)
    assert error.value.code == "environment_unsupported"
    session.open_reader.assert_not_called()
    session.open_writer.assert_not_called()


@pytest.mark.parametrize("action", ["read", "read-stream", "write"])
async def test_closed_session_transfer_setup_is_converted(action):
    files, _ = operator(methods=("file.open_reader", "file.open_writer"), failure=EIPSessionStateError("closed"))
    with pytest.raises(EnvironmentError) as error:
        await invoke_transfer(files, action)
    assert error.value.code == "environment_unavailable"


@pytest.mark.parametrize("action", ["read", "read-stream", "write"])
async def test_cancelled_transfer_setup_preserves_cancellation(action):
    files, _ = operator(methods=("file.open_reader", "file.open_writer"), failure=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await invoke_transfer(files, action)


@pytest.mark.parametrize("maximum", [0, -1])
async def test_text_read_rejects_invalid_budget_before_dispatch(maximum):
    files, session = operator()
    session.client = SimpleNamespace(file_read_text=AsyncMock())
    with pytest.raises(EnvironmentError) as error:
        await files.read_text("/file", max_bytes=maximum)
    assert error.value.code == "environment_request_invalid"
    session.client.file_read_text.assert_not_called()


@pytest.mark.parametrize("maximum", [None, 512])
async def test_text_read_forwards_caller_budget_and_line_observation(maximum):
    files, session = operator()
    observed = SimpleNamespace(
        info=SimpleNamespace(path=eip.EIPPath(path="/file")),
        text="中文\n",
        line_offset=2,
        lines_read=1,
        has_more=True,
        truncated_lines=(3,),
    )
    session.client = SimpleNamespace(file_read_text=AsyncMock(return_value=observed))
    result = await files.read_text("/file", line_offset=2, line_limit=1000, max_bytes=maximum)
    params = session.client.file_read_text.call_args.args[0]
    assert params.text_byte_limit == maximum
    assert params.line_offset == 2 and params.line_limit == 1000
    assert result.text == observed.text
    assert result.lines_read == 1 and result.has_more
    assert result.truncated_lines == (3,)


@pytest.mark.parametrize("action", ["read", "stat", "write"])
async def test_closed_session_control_setup_is_converted(action):
    class ClosedSession:
        descriptor = SimpleNamespace(working_directory="/")

        @property
        def client(self):
            raise EIPSessionStateError("closed")

    files = EIPFileOperator(session=ClosedSession(), environment_id="env", execution_id="workspace", generation="1")
    with pytest.raises(EnvironmentError) as error:
        if action == "read":
            await files.read_text("/file")
        elif action == "stat":
            await files.stat("/file")
        else:
            await files.write_text("/file", "replacement", mode="replace")
    assert error.value.code == "environment_unavailable"


def discovery_fragment(text="中文\n", *, part=None, offset=0, path="/skills/a/SKILL.md"):
    import hashlib

    return eip.FileDocumentText(
        kind="text",
        prefix=eip.FileReadTextResult(
            info=eip.FileInfo(path=eip.EIPPath(path=path), kind=eip.FileKind.FILE, size_bytes=len(text.encode())),
            text=text if part is None else part,
            line_offset=0,
            lines_read=1,
            has_more=False,
        ),
        text_byte_offset=offset,
        prefix_byte_length=len(text.encode()),
        prefix_digest=eip.ContentDigest(algorithm="sha256", value=hashlib.sha256(text.encode()).hexdigest()),
    )


def discovery_operator(pages):
    files, session = operator(methods=("file.discover_documents",))
    session.max_response_bytes = 8192
    session.descriptor.limits = SimpleNamespace(max_response_bytes=16384)
    session.client = SimpleNamespace(file_discover_documents=AsyncMock(side_effect=pages))
    return files, session


async def test_document_discovery_assembles_contiguous_utf8_prefix_without_extra_calls():
    from a13n_harness.environment.files import FileDocumentRequest, FileDocumentText

    position = eip.FileDiscoveryPosition(candidate_offset=1, text_byte_offset=3)
    pages = [
        eip.FileDiscoverDocumentsResult(
            documents=(discovery_fragment(part="中"),),
            next_position=position,
            entries_exceeded=False,
            omitted_unrepresentable_entries=0,
        ),
        eip.FileDiscoverDocumentsResult(
            documents=(discovery_fragment(part="文\n", offset=3),),
            entries_exceeded=False,
            omitted_unrepresentable_entries=0,
        ),
    ]
    files, session = discovery_operator(pages)
    values = [item async for item in files.discover_documents(FileDocumentRequest(root="/skills", basename="SKILL.md"))]
    assert len(values) == 1 and isinstance(values[0], FileDocumentText)
    assert values[0].prefix.text == "中文\n" and values[0].info.kind == "file"
    calls = session.client.file_discover_documents.call_args_list
    assert len(calls) == 2 and calls[1].args[0].position == position
    assert all(call.args[0].result_byte_limit == 4096 for call in calls)


@pytest.mark.parametrize("damage", ["offset", "digest", "metadata", "unfinished", "nonprogress", "outside", "empty"])
async def test_document_discovery_rejects_inconsistent_fragments(damage):
    from a13n_harness.environment.files import FileDocumentRequest

    first = discovery_fragment(part="中")
    second = discovery_fragment(part="文\n", offset=3)
    position = eip.FileDiscoveryPosition(candidate_offset=1, text_byte_offset=3)
    if damage == "offset":
        second = second.model_copy(update={"text_byte_offset": 2})
    elif damage == "digest":
        second = second.model_copy(update={"prefix_digest": eip.ContentDigest(algorithm="sha256", value="0" * 64)})
    elif damage == "metadata":
        second = second.model_copy(update={"prefix": second.prefix.model_copy(update={"has_more": True})})
    elif damage == "nonprogress":
        position = eip.FileDiscoveryPosition(candidate_offset=0, text_byte_offset=0)
    elif damage == "outside":
        first = discovery_fragment(path="/elsewhere/a/SKILL.md")
    elif damage == "empty":
        first = discovery_fragment(part="")
    pages = [
        eip.FileDiscoverDocumentsResult(
            documents=(first,),
            next_position=position if damage != "unfinished" else None,
            entries_exceeded=False,
            omitted_unrepresentable_entries=0,
        ),
        eip.FileDiscoverDocumentsResult(documents=(second,), entries_exceeded=False, omitted_unrepresentable_entries=0),
    ]
    files, _ = discovery_operator(pages)
    with pytest.raises(EnvironmentError) as error:
        _ = [item async for item in files.discover_documents(FileDocumentRequest(root="/skills", basename="SKILL.md"))]
    assert error.value.code in {"environment_provider_failure", "environment_conflict"}


@pytest.mark.parametrize(
    "exceeded,omitted,code", [(True, 0, "environment_too_large"), (False, 1, "environment_unsupported")]
)
async def test_document_discovery_incomplete_roots_raise(exceeded, omitted, code):
    from a13n_harness.environment.files import FileDocumentRequest

    files, _ = discovery_operator(
        [eip.FileDiscoverDocumentsResult(entries_exceeded=exceeded, omitted_unrepresentable_entries=omitted)]
    )
    with pytest.raises(EnvironmentError) as error:
        _ = [item async for item in files.discover_documents(FileDocumentRequest(root="/skills", basename="SKILL.md"))]
    assert error.value.code == code
