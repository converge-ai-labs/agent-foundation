"""Docker HTTP listener defaults and explicit deployment overrides."""

import pytest
from a13n_harness.providers.environment.docker.connection import DockerHttpConfiguration


@pytest.mark.parametrize(
    "host,expected",
    [
        ("127.0.0.1", "127.0.0.1"),
        ("localhost", "127.0.0.1"),
        ("[::1]", "::1"),
        ("10.0.0.8", "10.0.0.8"),
        ("[fd00::8]", "fd00::8"),
    ],
)
def test_automatic_listen_address(host, expected):
    config = DockerHttpConfiguration(host=host, allow_plaintext_private_link=True)
    assert config.bind_host is None
    assert config.listen_address == expected
    assert DockerHttpConfiguration.model_validate(config.model_dump()).listen_address == expected


def test_remote_automatic_listener_requires_consent():
    with pytest.raises(ValueError, match="consent"):
        DockerHttpConfiguration(host="10.0.0.8")


def test_hostname_requires_explicit_listener_without_dns_guessing():
    with pytest.raises(ValueError, match="Hostnames require a listen address"):
        DockerHttpConfiguration(host="desktop.internal", allow_plaintext_private_link=True)
    assert (
        DockerHttpConfiguration(
            host="desktop.internal", bind_host="10.0.0.8", allow_plaintext_private_link=True
        ).listen_address
        == "10.0.0.8"
    )


def test_existing_explicit_listener_is_preserved():
    config = DockerHttpConfiguration(host="10.0.0.8", bind_host="127.0.0.1", allow_plaintext_private_link=True)
    assert config.listen_address == "127.0.0.1"
