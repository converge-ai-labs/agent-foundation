"""Disposable, scripted WebUI for hands-on design review. No external models."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn
import yaml
from a13n_harness_ui import model_catalog
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.composition.reconstruction import AgentReconstructor
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings
from a13n_harness_ui.surfaces import NewThreadDefaults
from a13n_harness_ui.webui import create_webui
from pydantic_ai.capabilities import Capability
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import ModelRequest, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import DeltaThinkingPart, DeltaToolCall, FunctionModel
from pydantic_ai.toolsets import FunctionToolset

ANSWER = """The checkout flow has a small but important gap: a slow request can submit the same order twice.

### What I found

The form validates the address correctly, but the **Place order** button stays enabled while payment is pending. A second click creates another request with a new identifier.

### A focused fix

1. Disable submission while a request is in flight.
2. Reuse the same idempotency key when retrying that order.
3. Keep the form values if the payment service is unavailable.

```typescript
async function placeOrder(order: Order) {
  if (pending) return;
  setPending(true);
  try {
    await checkout.submit(order, { idempotencyKey: order.id });
  } finally {
    setPending(false);
  }
}
```

| Scenario | Expected behavior | Result |
| :--- | :--- | :--- |
| Double click | One order created | Passed |
| Slow response | Clear pending feedback | Passed |
| Network failure | Form retained for retry | Passed |

The changes are ready to review. I would keep the retry behavior explicit so a failed payment never looks like a successful order.
"""


def seed_workspace(workspace: Path) -> None:
    """Create a fictional repository with readable files and reviewable changes."""
    workspace.mkdir(exist_ok=True)
    files = {
        "README.md": "# Atlas\n\nA fictional storefront for interface review.\n",
        "checkout.ts": "export async function placeOrder(order) {\n  await checkout.submit(order);\n}\n",
        "docs/release-notes.md": "# Release notes\n\nCheckout reliability improvements.\n",
        "src/components/checkout/validation.ts": "export const validAddress = (address: string) => address.trim().length > 0;\n",
    }
    for name, content in files.items():
        target = workspace / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    environment = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Design review",
        "GIT_AUTHOR_EMAIL": "review@example.invalid",
        "GIT_COMMITTER_NAME": "Design review",
        "GIT_COMMITTER_EMAIL": "review@example.invalid",
    }
    for args in (
        ["init", "-q"],
        ["add", "."],
        ["-c", "commit.gpgsign=false", "commit", "-qm", "Seed fictional storefront"],
    ):
        subprocess.run(["git", *args], cwd=workspace, env=environment, check=True)
    (workspace / "checkout.ts").write_text(
        "let pending = false;\n\nexport async function placeOrder(order) {\n"
        "  if (pending) return;\n  pending = true;\n  try {\n"
        "    await checkout.submit(order, { idempotencyKey: order.id });\n"
        "  } finally {\n    pending = false;\n  }\n}\n"
    )
    (workspace / "docs/release-notes.md").write_text(
        "# Release notes\n\n## Checkout\n\n- Ignore duplicate submissions.\n- Keep the form after a network error.\n"
    )
    (workspace / "src/components/checkout/retry-policy-with-a-deliberately-long-file-name.ts").write_text(
        "export const retryPolicy = { maxAttempts: 3, preserveInput: true };\n"
    )


def configuration(root: Path) -> Path:
    workspace = root / "workspace"
    seed_workspace(workspace)
    docs = {
        "a13n-harness-ui.yaml": {
            "schema_version": "1",
            "defaults": {"agent": "agent-review"},
            "memory": {"auto_organize": {"enabled": False}},
        },
        "models/review.yaml": {
            "schema_version": "1",
            "kind": "model",
            "id": "model-review",
            "name": "GPT-5",
            "route": "openai-responses:gpt-5",
            "model_configuration": {"base_url": "https://api.openai.com/v1"},
            "authentication": {"kind": "api_key", "env": "REVIEW_MODEL_KEY"},
        },
        "agents/review.yaml": {
            "schema_version": "1",
            "kind": "agent",
            "id": "agent-review",
            "name": "Builder",
            "model": "model-review",
        },
        "projects/atlas.yaml": {
            "schema_version": "1",
            "kind": "project",
            "id": "project-atlas",
            "name": "Atlas storefront",
            "roots": [{"path": str(workspace)}],
        },
        "projects/research.yaml": {
            "schema_version": "1",
            "kind": "project",
            "id": "project-research",
            "name": "Research notebook",
            "roots": [{"path": str(workspace)}],
        },
    }
    for name, value in docs.items():
        path = root / "config" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(value))
    seed_memory(root / "config")
    return root / "config/a13n-harness-ui.yaml"


def seed_memory(configuration_root: Path) -> None:
    """Populate fictional read-only browsing examples without invoking a model."""
    files = {
        "global/MEMORY.md": "# Working preferences\n\nKeep summaries concise, explain important tradeoffs, and include the checks that were run.\n\n## Communication\n\nUse plain language. Put the result first and keep supporting detail close to the relevant decision.\n",
        "projects/project-atlas/MEMORY.md": "# Atlas storefront\n\nAtlas is a fictional storefront used for interface review.\n\n## Project conventions\n\n- Keep checkout retries explicit and preserve the form after a failed request.\n- Reuse the order identifier as the idempotency key.\n- Run focused checks before changing the payment flow.\n\n## More detail\n\nCheckout decisions are recorded in `checkout.md`.\n",
        "projects/project-atlas/checkout.md": "# Checkout decisions\n\n## Submission and retries\n\nDisable submission while a request is in flight. Retrying a failed order should reuse its identifier so the payment provider can reject duplicates.\n\n## Failure feedback\n\nKeep the original address and payment choices visible. Explain what failed and provide a clear retry action.\n\n## Review checklist\n\n1. Double-click creates one order.\n2. A slow response keeps the pending state visible.\n3. A network failure preserves the form.\n",
    }
    for name, text in files.items():
        target = configuration_root / "memory" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)


async def model(messages, info):
    last_input = max(
        (
            i
            for i, message in enumerate(messages)
            if isinstance(message, ModelRequest) and any(isinstance(part, UserPromptPart) for part in message.parts)
        ),
        default=0,
    )
    turn = messages[last_input:]
    prompt = " ".join(
        str(part.content)
        for message in turn
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart)
    )
    returned = any(
        isinstance(part, ToolReturnPart)
        for message in turn
        if isinstance(message, ModelRequest)
        for part in message.parts
    )
    if "[failure]" in prompt:
        yield "I'm checking the deployment configuration.\n\n"
        raise UsageLimitExceeded("The review model reached its request limit. Your chat and input are saved.")
    if "[question]" in prompt and not returned:
        yield {
            0: DeltaToolCall(
                name="ask_user_question",
                tool_call_id="review-question",
                json_args=json.dumps(
                    {
                        "questions": [
                            {
                                "header": "Scope",
                                "question": "Which part should I review first?",
                                "options": [
                                    {"label": "Checkout", "description": "Payment and order submission"},
                                    {
                                        "label": "Navigation",
                                        "description": "Finding products and returning to your cart",
                                    },
                                ],
                            }
                        ]
                    }
                ),
            )
        }
        return
    if not returned:
        yield "I'll trace the checkout flow, check how it handles retries, and run the focused checks.\n\n"
        yield {
            0: DeltaThinkingPart(
                content="I should inspect the request boundary before proposing a change. The important cases are duplicate submissions, slow responses, and recoverable failures."
            )
        }
        await asyncio.sleep(0.3)
        yield {
            1: DeltaToolCall(name="view", tool_call_id="review-read", json_args='{"path":"checkout.ts"}'),
            2: DeltaToolCall(
                name="shell_exec", tool_call_id="review-check", json_args='{"command":"pnpm test checkout"}'
            ),
            3: DeltaToolCall(
                name="note_write",
                tool_call_id="review-note",
                json_args='{"key":"Review focus","value":"Keep retries explicit and preserve the form after a failed request."}',
            ),
        }
        return
    answer = ANSWER
    if "[long]" in prompt:
        answer += "\n### Detailed observations\n\n" + "\n\n".join(
            f"**Case {i + 1}.** A customer should be able to understand whether the order is still processing, failed, or completed. Keep the original input available and make the next action clear."
            for i in range(18)
        )
    for chunk in answer.split("\n\n"):
        yield chunk + "\n\n"
        await asyncio.sleep(0.5 if "[slow]" in prompt else 0.01)


async def serve(root: Path, port: int):
    path = configuration(root)
    original = AgentReconstructor.reconstruct

    def reconstruct(self, composition, *, root_capabilities=(), **kwargs):
        tools = FunctionToolset(id="design-review")

        @tools.tool_plain
        def view(path: str) -> str:
            """Read a fictional source file."""
            return "export async function placeOrder(order) {\n  await checkout.submit(order);\n}\n"

        @tools.tool_plain
        def shell_exec(command: str) -> dict[str, object]:
            """Return scripted check results; never execute a shell command."""
            return {
                "stdout": "✓ rejects duplicate submissions\n✓ preserves input after failure\n✓ shows payment progress\n\n3 tests passed",
                "exit_code": 0,
            }

        return original(
            self,
            composition,
            root_capabilities=(*root_capabilities, Capability(id="design-review", toolsets=(tools,))),
            **kwargs,
        )

    async def resolve(self, context, model_id):
        return FunctionModel(stream_function=model)

    async def directory():
        return model_catalog.bundled_models()

    AgentReconstructor.reconstruct = reconstruct
    HarnessUiModelResolver.__call__ = resolve
    model_catalog.fetch_directory = directory
    settings = HarnessUiSettings(storage=StorageSettings(data_root=root / "data"), pricing_auto_update=False)

    @asynccontextmanager
    async def app_context():
        async with open_harness_ui_app(
            settings, configuration_path=path, host_mode="webui", share_computer=True
        ) as app:
            status = await app.status()
            if status.candidate_error_message:
                raise RuntimeError(status.candidate_error_message)
            for title, project, prompt in (
                (
                    "Review the checkout flow",
                    "project-atlas",
                    "Review the checkout flow for duplicate requests and error recovery.",
                ),
                ("Plan the next release", "project-atlas", "[long] Create a detailed review of the next release."),
                ("Investigate deployment failure", "project-atlas", "[failure] Check the deployment configuration."),
                (
                    "Compare onboarding approaches",
                    "project-research",
                    "Review the onboarding approach and recommend next steps.",
                ),
                ("A fresh idea", "project-research", None),
            ):
                thread = await app.create_thread(title=title, defaults=NewThreadDefaults(project_id=project))
                if prompt:
                    receipt = await app.submit_thread(thread_id=thread.thread_id, prompt=prompt)
                    operation = await app.wait_root_operation(receipt.receipt_id, timeout_seconds=30)
                    expected = "failed" if "[failure]" in prompt else "completed"
                    if operation.status != expected:
                        raise RuntimeError(f"Fixture {title!r} ended with {operation.status}, expected {expected}")
                print(f"  {title}: /threads/{thread.thread_id}", flush=True)
            print(f"Review API: http://127.0.0.1:{port} (key: test-only-key)", flush=True)
            print(
                "All responses and tool results are scripted. Try [slow], [long], [failure], or [question].", flush=True
            )
            yield app

    server = create_webui(app_context, api_key="test-only-key")
    await uvicorn.Server(uvicorn.Config(server, host="127.0.0.1", port=port, log_level="warning")).serve()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8876)
    args = parser.parse_args()
    with TemporaryDirectory(prefix="harness-ui-review-") as directory:
        root = Path(directory)
        # Isolate all configuration, history, native files, and account discovery.
        os.environ.update(
            HOME=directory,
            USERPROFILE=directory,
            XDG_CONFIG_HOME=str(root / "xdg"),
            CODEX_HOME=str(root / "codex"),
            GROK_HOME=str(root / "grok"),
            GROK_AUTH_PATH=str(root / "grok/auth.json"),
        )
        asyncio.run(serve(root, args.port))


if __name__ == "__main__":
    main()
