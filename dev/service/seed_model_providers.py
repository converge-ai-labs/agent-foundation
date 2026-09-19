"""Fictional connections for exercising every supported Provider editor."""

from a13n_service.models.providers import built_in_model_provider_catalog
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from .seed_client import Client

MODEL_EXAMPLES = {
    "openai": ("GPT-5.6", "openai", "gpt-5.6"),
    "anthropic": ("Claude Opus 5", "anthropic", "claude-opus-5"),
    "google_gemini": ("Gemini 3.8 Flash", "google", "gemini-3.8-flash"),
    "google_vertex": ("Gemini 3.8 Flash", "google-vertex", "gemini-3.8-flash"),
    "azure_openai": ("GPT-5.6 Terra", "azure", "gpt-5.6-terra"),
    "aws_bedrock": ("Claude Opus 5", "amazon-bedrock", "anthropic.claude-opus-5"),
    "openrouter": ("Claude Opus 5", "openrouter", "anthropic/claude-opus-5"),
    "ollama": ("Llama 3.2", None, "llama3.2"),
    "alibaba_model_studio": ("Qwen 3.8 Flash", "alibaba", "qwen3.8-flash"),
    "deepseek": ("DeepSeek V4 Pro", "deepseek", "deepseek-v4-pro"),
    "moonshot": ("Kimi K3", "moonshotai", "kimi-k3"),
    "minimax": ("MiniMax-M3", "minimax", "MiniMax-M3"),
    "zhipu": ("GLM-5.3", "zhipuai", "glm-5.3"),
}


async def seed_model_providers(client: Client, base: str) -> dict[str, str]:
    existing = {item["name"]: item["id"] for item in await client.collection(base + "/model-providers")}
    result = {}
    for definition in built_in_model_provider_catalog().values():
        name = f"{definition.display_name} · demo credentials"
        if name in existing:
            result[definition.type] = existing[name]
            continue
        configuration = {
            "google_vertex": {"project_id": "fictional-local-demo", "location": "us-central1"},
            "aws_bedrock": {"region": "us-east-1"},
            "azure_openai": {"base_url": "https://example.com/v1"},
            "ollama": {"base_url": "http://127.0.0.1:11434"},
            "alibaba_model_studio": {"region": "cn-beijing", "domain_type": "mainland_china"},
        }.get(definition.type, {})
        credential = {"api_key": "fictional-local-demo-not-a-real-api-key"}
        if definition.type == "ollama":
            credential = None
        elif definition.type == "aws_bedrock":
            credential = {"aws_access_key_id": "FICTIONALLOCALDEMO", "aws_secret_access_key": "not-a-real-aws-secret"}
        elif definition.type == "google_vertex":
            # A syntactically valid, unregistered key; never a real cloud account.
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            credential = {
                "type": "service_account",
                "project_id": "fictional-local-demo",
                "client_email": "demo@fictional-local-demo.iam.gserviceaccount.com",
                "token_uri": "https://oauth2.googleapis.com/token",
                "private_key": key.private_bytes(
                    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
                ).decode(),
            }
        item = await client.request(
            "POST",
            base + "/model-providers",
            expected=201,
            json={
                "type": definition.type,
                "name": name,
                "configuration": configuration,
                "credential": credential,
            },
        )
        result[definition.type] = item["id"]
    return result


async def seed_provider_models(client: Client, base: str, providers: dict[str, str]) -> dict[str, str]:
    """One disabled, fictional-credential Model for each built-in Provider."""
    definitions = built_in_model_provider_catalog().values()
    existing = {item["key"]: item["id"] for item in await client.collection(base + "/models")}
    keys = {definition.type: f"demo-{definition.type.replace('_', '-')}" for definition in definitions}
    if all(key in existing for key in keys.values()):
        return {provider_type: existing[key] for provider_type, key in keys.items()}
    catalog = await client.request("GET", base + "/model-catalog")
    catalog_entries = {(item["ref"]["provider"], item["ref"]["model"]): item for item in catalog["items"]}
    result = {}
    for definition in definitions:
        name, catalog_provider, upstream_model = MODEL_EXAMPLES[definition.type]
        key = keys[definition.type]
        if key in existing:
            result[definition.type] = existing[key]
            continue
        reference = {"provider": catalog_provider, "model": upstream_model} if catalog_provider else None
        catalog_entry = catalog_entries.get((catalog_provider, upstream_model))
        item = await client.request(
            "POST",
            base + "/models",
            expected=201,
            json={
                "key": key,
                "name": name,
                "provider_id": providers[definition.type],
                "upstream_model": upstream_model,
                "catalog_ref": reference,
                "model_api": definition.supported_model_apis[0],
                "declarations": catalog_entry["declarations"] if catalog_entry else {},
                "enabled": False,
            },
        )
        result[definition.type] = item["id"]
    return result
