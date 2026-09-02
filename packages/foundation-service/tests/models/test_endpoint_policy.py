import ipaddress

import pytest
from a13n_service.models.endpoint_policy import EndpointPolicy, EndpointPolicyError


@pytest.mark.parametrize(
    "endpoint",
    (
        "ftp://models.example.com",
        "https://user:password@models.example.com",
        "https://models.example.com/path#credential",
        "https://models.example.com?api_key=secret",
        "https://models.example.com?token=secret",
    ),
)
def test_endpoint_syntax_rejects_credential_and_unsupported_forms(endpoint: str) -> None:
    with pytest.raises(EndpointPolicyError):
        EndpointPolicy().validate_syntax(endpoint)


def test_endpoint_is_normalized_without_changing_safe_query() -> None:
    normalized, hostname, port = EndpointPolicy().validate_syntax(
        "HTTPS://Models.Example.COM:443/v1/?api-version=2026-01-01"
    )

    assert normalized == "https://models.example.com/v1?api-version=2026-01-01"
    assert hostname == "models.example.com"
    assert port == 443


@pytest.mark.parametrize(
    "address",
    ("127.0.0.1", "::1", "169.254.169.254", "100.100.100.200", "10.0.0.2", "192.168.1.2"),
)
def test_endpoint_policy_denies_local_metadata_and_private_addresses(address: str) -> None:
    with pytest.raises(EndpointPolicyError):
        EndpointPolicy().validate_address("models.example.com", ipaddress.ip_address(address))


def test_operator_domain_or_network_allowlist_can_admit_private_destination() -> None:
    by_domain = EndpointPolicy.from_operator_allowlist(private_domains=("models.internal",))
    by_network = EndpointPolicy.from_operator_allowlist(private_cidrs=("10.10.0.0/16",))

    by_domain.validate_address("tenant.models.internal", ipaddress.ip_address("10.0.0.2"))
    by_network.validate_address("arbitrary.internal", ipaddress.ip_address("10.10.2.3"))


@pytest.mark.anyio
async def test_literal_public_endpoint_validates_without_dns() -> None:
    assert await EndpointPolicy().validate("https://8.8.8.8/v1") == "https://8.8.8.8/v1"
