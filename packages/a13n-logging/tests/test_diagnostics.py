import errno
import json
from types import SimpleNamespace

from a13n_logging import exception_details


def test_suppressed_chain_preserves_root_stack_without_execution_content():
    secret = "secret-token-and-private-payload"
    try:
        try:
            raise ConnectionRefusedError(errno.ECONNREFUSED, secret)
        except ConnectionRefusedError:
            raise RuntimeError(secret) from None
    except RuntimeError as error:
        error.add_note(secret)
        details = exception_details(error)
    assert [item["type"] for item in details] == ["builtins.RuntimeError", "builtins.ConnectionRefusedError"]
    assert details[1]["parent"] == 0
    assert details[1]["errno"] == errno.ECONNREFUSED
    assert (
        details[1]["frames"][-1]["function"] == "test_suppressed_chain_preserves_root_stack_without_execution_content"
    )
    assert secret not in json.dumps(details)


def test_group_children_http_status_and_cycles_are_safe_and_bounded():
    child = RuntimeError("private-response")
    child.response = SimpleNamespace(status_code=503, text="private-response")
    peer = ValueError("private-payload")
    group = ExceptionGroup("private-group", [child, peer])
    child.__context__ = group
    details = exception_details(group)
    assert len(details) == 3
    assert details[1]["status_code"] == 503
    assert details[2]["type"] == "builtins.ValueError"
    assert details[1]["parent"] == details[2]["parent"] == 0
    assert "private" not in json.dumps(details)
    error = RuntimeError()
    for _ in range(100):
        next_error = RuntimeError()
        next_error.__cause__ = error
        error = next_error
    assert len(exception_details(error)) == 32


def test_provider_properties_and_stringification_cannot_break_diagnostics():
    class ProviderError(Exception):
        @property
        def response(self):
            raise RuntimeError("response is unavailable")

        def __str__(self):
            raise AssertionError("must not serialize provider text")

    assert exception_details(ProviderError())[0]["type"].endswith(".ProviderError")
