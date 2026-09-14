"""Local OSS model configuration; no changes to Service product schemas."""

import os


def configure(config):
    dimensions = int(os.environ.get("MEM0_OSS_EMBEDDING_DIMENSIONS", "128"))
    if not 1 <= dimensions <= 16000:
        raise ValueError("MEM0_OSS_EMBEDDING_DIMENSIONS must be between 1 and 16000")
    for name, section in (("LLM", "llm"), ("EMBEDDING", "embedder")):
        prefix = f"MEM0_OSS_{name}_"
        base_url = os.environ[prefix + "BASE_URL"]
        config[section] = {
            "provider": "openai",
            "config": {
                "model": os.environ[prefix + "MODEL"],
                "api_key": os.environ[prefix + "API_KEY"],
                "openai_base_url": base_url,
            },
        }
    # Many OpenAI-compatible embedders reject the optional dimensions parameter.
    # The vector table always needs its actual size; API size overrides are opt-in.
    if os.environ.get("MEM0_OSS_EMBEDDING_SEND_DIMENSIONS", "false").lower() == "true":
        config["embedder"]["config"]["embedding_dims"] = dimensions
    config["vector_store"]["config"]["embedding_model_dims"] = dimensions
    return config


def verify_dimensions(memory):
    store = memory.vector_store
    store._ensure_collection()
    with store._get_cursor() as cursor:
        cursor.execute(
            "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
            "WHERE attrelid = %s::regclass AND attname = 'vector' AND NOT attisdropped",
            (store.collection_name,),
        )
        row = cursor.fetchone()
    if row is None or row[0] != f"vector({store.embedding_model_dims})":
        raise RuntimeError(
            "Mem0 embedding dimensions do not match the existing collection. "
            "Restore MEM0_OSS_EMBEDDING_DIMENSIONS or select a new MEM0_OSS_COLLECTION. "
            "Existing memories have not been deleted."
        )
