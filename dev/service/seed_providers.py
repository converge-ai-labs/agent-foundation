"""A fictional account for every provider type the Service offers, so every provider editor has data.

Credentials are placeholders that no backend accepts, and nothing here calls a provider. Each fictional model
provider serves one disabled model.
"""

from __future__ import annotations

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from dev.service.api import Api, Json

KINDS = ("model", "web", "connector", "environment")
FICTIONAL_KEY = "fictional-not-a-real-key"
# Configuration a type requires; other types need none.
CONFIG: dict[tuple[str, str], Json] = {
    ("model", "google_vertex"): {"project_id": "northstar-fictional", "location": "us-central1"},
    ("model", "azure_openai"): {"resource_endpoint": "https://northstar-fictional.openai.azure.com"},
    ("model", "aws_bedrock"): {"region": "us-east-1"},
    ("model", "ollama"): {"base_url": "http://127.0.0.1:11434"},
    ("model", "alibaba_model_studio"): {"region": "cn-beijing", "domain_type": "mainland_china"},
    ("environment", "daytona"): {"organization_id": "northstar-fictional"},
    ("environment", "modal"): {"workspace": "northstar-fictional", "app_name": "a13n-fictional"},
    ("environment", "runloop"): {"organization": "northstar-fictional"},
    ("environment", "sprites"): {"organization": "northstar-fictional"},
    ("environment", "vercel"): {"team_id": "team_fictional", "project_id": "prj_fictional"},
}
DISABLED = "minimax"
EXTRA_HEADERS = {"openrouter": {"x-title": "Northstar Studio"}}
# A cataloged model each type serves; the Service records a catalog reference as given, never resolving it.
MODELS = {
    "openai": ("GPT-5.6", "gpt-5.6"),
    "anthropic": ("Claude Opus 5", "claude-opus-5"),
    "google_gemini": ("Gemini 3.8 Flash", "gemini-3.8-flash"),
    "google_vertex": ("Gemini 3.8 Flash", "gemini-3.8-flash"),
    "azure_openai": ("GPT-5.6 Terra", "gpt-5.6-terra"),
    "aws_bedrock": ("Claude Opus 5", "anthropic.claude-opus-5"),
    "openrouter": ("Claude Opus 5", "anthropic/claude-opus-5"),
    "ollama": ("Llama 3.2", "llama3.2"),
    "alibaba_model_studio": ("Qwen3.8 Flash", "qwen3.8-flash"),
    "deepseek": ("DeepSeek V4 Pro", "deepseek-v4-pro"),
    "moonshot": ("Kimi K3", "kimi-k3"),
    "minimax": ("MiniMax-M3", "MiniMax-M3"),
    "zhipu": ("GLM-5.3", "glm-5.3"),
}


def seed_providers(api: Api) -> dict[str, dict[str, Json]]:
    """The fictional accounts by kind and type; `local` environments are seeded for real (seed_local.py)."""
    accounts: dict[str, dict[str, Json]] = {kind: {} for kind in KINDS}
    for kind in KINDS:
        for described in api.items(f"/api/v1/provider-types/{kind}"):
            if (kind, described["type"]) == ("environment", "local"):
                continue
            provider = _account(api, kind, described)
            if kind == "model":
                _model(api, provider, described)
            accounts[kind][described["type"]] = provider
    if disabled := accounts["model"].get(DISABLED):
        path = f"/api/v1/model-providers/{disabled['id']}"
        accounts["model"][DISABLED] = api.patch(path, disabled, {"enabled": False})
    return accounts


def _account(api: Api, kind: str, described: Json) -> Json:
    provider_type = described["type"]
    credential = _credential(described)
    body = {
        "type": provider_type,
        # A type without a credential, such as `docker`, is a real account of this machine.
        "name": described["display_name"] if credential is None else f"{described['display_name']} (fictional)",
        "config": CONFIG.get((kind, provider_type), {}),
        "credential": credential,
        "extra_headers": EXTRA_HEADERS.get(provider_type, {}),
    }
    return api.post(f"/api/v1/{kind}-providers", body)


def _credential(described: Json) -> Json | None:
    if described["credential_schema"] is None or described["authentication"]["mode"] == "forbidden":
        return None
    match described["type"]:
        case "google_vertex":
            return {
                "type": "service_account",
                "project_id": "northstar-fictional",
                "client_email": "seed@northstar-fictional.iam.gserviceaccount.com",
                "private_key": _unregistered_key(),
            }
        case "aws_bedrock":
            return {"aws_access_key_id": "AKIAFICTIONAL", "aws_secret_access_key": FICTIONAL_KEY}
        case "modal":
            return {"token_id": "ak-fictional", "token_secret": FICTIONAL_KEY}
        case _:
            return {"api_key": FICTIONAL_KEY}


def _unregistered_key() -> str:
    """A syntactically valid service-account key that no cloud account knows."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    return pem.decode()


def _model(api: Api, provider: Json, described: Json) -> Json:
    """One disabled model, referring to its catalog entry where the type's models are cataloged. Types share
    upstream names, so the key names the type rather than defaulting to the upstream name."""
    name, upstream = MODELS.get(provider["type"], ("Fictional model", "fictional-model"))
    channels = described.get("catalog_providers") or []
    body = {
        "provider_id": provider["id"],
        "key": f"fictional-{provider['type'].replace('_', '-')}",
        "name": name,
        "config": {"model_name": upstream, "model_api": described["default_model_api"]},
        "catalog_ref": {"provider": channels[0], "model": upstream} if channels else None,
        "enabled": False,
    }
    return api.post("/api/v1/models", body)
