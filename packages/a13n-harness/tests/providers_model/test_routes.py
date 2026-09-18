from __future__ import annotations

import pytest
from a13n_harness.providers.model.credentials import ApiKeyCredential, GoogleServiceAccount
from a13n_harness.providers.model.routes import build_api_key_model
from pydantic import ValidationError
from pydantic_ai.models.google import GoogleModel


@pytest.mark.anyio
@pytest.mark.parametrize("provider", ["google", "gemini", "google-gla", "google-vertex", "google-cloud"])
async def test_google_routes_preserve_developer_and_cloud_transports(provider):
    model = await build_api_key_model(
        f"{provider}:gemini-2.5-pro", ApiKeyCredential(api_key="fixture"), base_url="https://127.0.0.1"
    )
    async with model:
        assert isinstance(model, GoogleModel)
        assert model.client.vertexai is (provider != "google")


def test_credentials_reject_blank_keys_and_invalid_service_account_pem():
    with pytest.raises(ValidationError, match="API key must not be blank"):
        ApiKeyCredential(api_key="  ")
    with pytest.raises(ValidationError, match="valid service-account key"):
        GoogleServiceAccount(project_id="fixture", client_email="fixture@example.com", private_key="invalid-pem")
