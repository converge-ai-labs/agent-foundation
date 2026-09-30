"""Shared Harness-owned input projection for protocol consumers."""

from a13n_harness.content import ContentMetadata as ContentMetadata
from a13n_harness.content import project_input_content as project_input_content

AUTHORED_INPUT_EVENT_NAMES: frozenset[str] = frozenset({"a13n.input.user", "a13n.input.steering"})
