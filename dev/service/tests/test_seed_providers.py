"""Providers sharing a vendor name retain their domain's seed configuration."""

from unittest.mock import Mock

from a13n_environment.vercel.provider import VERCEL
from a13n_harness.providers.model.vercel import DEFINITION as VERCEL_GATEWAY

from dev.service.api import Api
from dev.service.seed_providers import _account


def test_vercel_gateway_and_sandbox_seed_separate_configurations() -> None:
    api = Mock(spec=Api)
    described = {
        "type": "vercel",
        "display_name": "Vercel",
        "credential_schema": {},
        "authentication": {"mode": "required"},
    }
    for kind, definition, expected in (
        ("model", VERCEL_GATEWAY, {}),
        ("environment", VERCEL, {"team_id": "team_fictional", "project_id": "prj_fictional"}),
    ):
        _account(api, kind, described)
        path, body = api.post.call_args.args
        assert path == f"/api/v1/{kind}-providers"
        assert body["config"] == expected
        definition.configuration_model.model_validate(body["config"])
