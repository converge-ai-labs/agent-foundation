from a13n_stream_protocol import AUTHORED_INPUT_EVENT_NAMES


def test_authored_input_event_names_exclude_generated_sources() -> None:
    assert AUTHORED_INPUT_EVENT_NAMES == frozenset({"a13n.input.user", "a13n.input.steering"})
