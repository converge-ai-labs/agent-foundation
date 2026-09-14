"""Offline checks of benchmark data fidelity and reported sample semantics."""

import pytest

from ..harness_integration.long_session_model import history_evidence, response_content


@pytest.mark.parametrize("padding", [32, 1024, 2048])
def test_model_usage_grows_with_real_request_size_and_summary_retains_memory(padding):
    memory = "a" * 32
    body = {"messages": [{"role": "user", "content": f"LONG_SESSION_MEMORY {memory}\nLONG_SESSION_RUN 1"}]}
    _, _, initial = response_content(body, "b" * 32, padding)
    for sequence in range(2, 51):
        body["messages"].append({"role": "user", "content": f"LONG_SESSION_RUN {sequence}\n" + "x" * padding})
    _, compact, grown = response_content(body, "b" * 32, padding)
    assert not compact and grown["prompt_tokens"] > initial["prompt_tokens"]
    body["messages"].append(
        {"role": "user", "content": "Generate a compact continuation summary for the conversation history."}
    )
    answer, compact, _ = response_content(body, "b" * 32, padding)
    assert compact and history_evidence(answer) == {"memories": [memory], "sequence": 50, "compactions": 1}
    body["messages"] = [{"role": "assistant", "content": answer}, {"role": "user", "content": "LONG_SESSION_RUN 51"}]
    _, compact, reduced = response_content(body, "b" * 32, padding)
    assert not compact and reduced["prompt_tokens"] < grown["prompt_tokens"]


def test_model_rejects_missing_original_memory():
    with pytest.raises(ValueError, match="initial Run's memory"):
        response_content({"messages": [{"role": "user", "content": "LONG_SESSION_RUN 1000"}]}, "b" * 32, 1024)


def test_compaction_directive_before_the_trailing_runtime_context():
    memory = "a" * 32
    body = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"LONG_SESSION_MEMORY {memory}\nLONG_SESSION_RUN 44"},
                    {"type": "text", "text": "Generate a compact continuation summary for the conversation history."},
                ],
            },
            {"role": "user", "content": [{"type": "text", "text": "<agent-context>Current runtime</agent-context>"}]},
        ]
    }
    answer, compact, _ = response_content(body, "b" * 32, 1024)
    assert compact and history_evidence(answer)["memories"] == [memory]
