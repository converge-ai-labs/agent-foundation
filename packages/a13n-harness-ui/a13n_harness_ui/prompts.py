"""Release-owned system prompt, frozen separately from user instructions."""

from importlib.resources import files

DEFAULT_SYSTEM_PROMPT = files("a13n_harness_ui").joinpath("assets/system_prompt.md").read_text(encoding="utf-8").strip()
