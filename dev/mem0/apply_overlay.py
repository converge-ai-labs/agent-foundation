"""Apply the small, guarded server integration to the pinned upstream source."""

from pathlib import Path


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    if text.count(old) != 1:
        raise RuntimeError(f"Pinned Mem0 source changed: {path.name}")
    path.write_text(text.replace(old, new))


if __name__ == "__main__":
    main = Path("/app/main.py")
    replace_once(
        main,
        "initialize_state(DEFAULT_CONFIG)",
        "from configuration import configure, verify_dimensions\n"
        "initialize_state(configure(DEFAULT_CONFIG))\nverify_dimensions(get_memory_instance())",
    )
    replace_once(
        main,
        "app.include_router(auth_router.router)",
        "from pagination import router as pagination_router\n"
        "app.include_router(pagination_router)\napp.include_router(auth_router.router)",
    )
