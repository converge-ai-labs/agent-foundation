import pytest
from a13n_harness.execution_identity import format_execution_header


@pytest.mark.parametrize(
    "kind", ["Subagent notification", "Subagent result", "Subagent message", "Tool result", "Tool notification"]
)
def test_shared_execution_header(kind):
    assert format_execution_header(kind, "reviewer", "sa_42", "running") == (
        f"{kind} · reviewer · execution_id=sa_42 · running"
    )
