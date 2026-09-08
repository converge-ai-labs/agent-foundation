"""Detach native semantic input at the App admission boundary."""

from copy import deepcopy

from a13n_harness.input import RunInputValue, normalize_input

from a13n_harness_ui.errors import RunCoordinationError


def detach_input(prompt: RunInputValue) -> RunInputValue:
    value = normalize_input(prompt).value
    if (
        value is None
        or (isinstance(value, str) and not value.strip())
        or (isinstance(value, tuple) and all(isinstance(part, str) and not part.strip() for part in value))
    ):
        raise RunCoordinationError("A root message must not be blank.", code="run_input_invalid")
    # BinaryContent and sequence containers are mutable native objects. An
    # accepted operation must not observe the caller's subsequent draft edits.
    return deepcopy(value)
