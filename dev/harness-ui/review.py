"""Disposable, scripted WebUI for hands-on design review. No external models."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
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

ANSWER = r"""The checkout flow has a small but important gap: a slow request can submit the same order twice.

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

### Reliability model

With independent request failure probability $p$, one explicit retry succeeds with probability $1 - p^2$.

$$
\Pr(\text{success within } n \text{ attempts}) = \sum_{k=1}^{n} (1-p)p^{k-1} = 1-p^n
$$

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
            "browser": {"mode": "disabled"},
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
        "mcp/tinyfish.yaml": {
            "schema_version": "2",
            "kind": "mcp_server",
            "id": "mcp-tinyfish",
            "name": "TinyFish",
            "tools": ["search", "fetch_content"],
            "transport": {
                "command": sys.executable,
                "arguments": [str(Path(__file__).with_name("review_mcp.py").resolve())],
            },
        },
        "agents/research.yaml": {
            "schema_version": "1",
            "kind": "agent",
            "id": "agent-research",
            "name": "Research assistant",
            "model": "model-review",
            "mcp_servers": ["mcp-tinyfish"],
            "instructions": "Search the web and compare sources before presenting your findings.",
            "tool_search": {
                "groups": {
                    "web": {"description": "Search the web and fetch page content.", "mcp_servers": ["mcp-tinyfish"]}
                }
            },
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
            if isinstance(message, ModelRequest)
            and any(isinstance(part, UserPromptPart) for part in message.parts)
            and not any(isinstance(part, ToolReturnPart) for part in message.parts)
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
    returns = [
        part
        for message in turn
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]
    returned = bool(returns)
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
    if "[answer]" in prompt:
        yield "Use the same order identifier for retries. Disable **Place order** while the request is pending, and keep the form intact if it fails."
        return
    pause = 5 if "[slow]" in prompt else 1.4
    if not returned:
        yield "I'll trace the submission path, then check duplicate requests and retry behavior.\n\n"
        thoughts = [
            "First I need to distinguish a repeated click from a deliberate retry. Both should use the same order identifier, but the UI needs to explain whether a request is pending or has failed.",
            "The important boundary is the pending state: it must be set before the first asynchronous operation, and cleared on both success and failure.",
        ]
        if "[thought]" in prompt:
            thoughts += [
                f"Case {index + 1}: check the boundary between submission, provider acknowledgement, and UI recovery. Preserve the original address and cart; never treat an unknown payment state as a successful order. Inspect the recorded request identifier before proposing another attempt."
                for index in range(16)
            ]
        for thought in thoughts:
            yield {0: DeltaThinkingPart(content=thought + "\n\n")}
            await asyncio.sleep(pause / len(thoughts))
        yield {1: DeltaToolCall(name="view", tool_call_id="review-read", json_args='{"path":"checkout.ts"}')}
        return
    shells = [part for part in returns if part.tool_name == "shell_exec"]
    if not shells:
        yield "The submit handler has no pending guard. I'll run the focused checks before changing the retry path.\n\n"
        yield {
            0: DeltaThinkingPart(
                content="A small guard should stop duplicate clicks. The existing request identifier should survive a retry; generating a new one would defeat provider deduplication."
            )
        }
        await asyncio.sleep(pause)
        command = "pnpm test checkout --offline" if "[retry]" in prompt else "pnpm test checkout"
        yield {
            1: DeltaToolCall(name="shell_exec", tool_call_id="review-check", json_args=json.dumps({"command": command}))
        }
        return
    if "[retry]" in prompt and len(shells) == 1:
        yield "The offline check could not resolve the test dependencies. I'll retry with the available local runner.\n\n"
        await asyncio.sleep(pause)
        yield {
            0: DeltaToolCall(
                name="shell_exec", tool_call_id="review-retry", json_args='{"command":"pnpm test checkout"}'
            )
        }
        return
    if not any(part.tool_name == "note_write" for part in returns):
        yield "All three checks passed. I'm saving the retry decision for the next review.\n\n"
        yield {
            0: DeltaToolCall(
                name="note_write",
                tool_call_id="review-note",
                json_args='{"key":"Checkout retries","value":"Reuse the order identifier, reject duplicate clicks, and preserve form input after failure."}',
            )
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
        async def view(path: str) -> str:
            """Read a fictional source file."""
            await asyncio.sleep(0.25)
            return "export async function placeOrder(order) {\n  await checkout.submit(order);\n}\n"

        @tools.tool_plain
        async def shell_exec(command: str) -> dict[str, object]:
            """Return scripted check results; never execute a shell command."""
            await asyncio.sleep(1.2)
            if "--offline" in command:
                return {
                    "stdout": "",
                    "stderr": "The offline test runner could not resolve cached dependencies.",
                    "exit_code": 1,
                }
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
                    "Inspect a long thought",
                    "project-research",
                    "[thought] Review checkout edge cases in detail before recommending a fix.",
                ),
                (
                    "Recover a failed check",
                    "project-atlas",
                    "[retry] Check the checkout tests and recover if the first attempt fails.",
                ),
                ("A quick answer", "project-research", "[answer] Summarize the retry rule."),
                ("A fresh idea", "project-research", None),
            ):
                thread = await app.create_thread(title=title, defaults=NewThreadDefaults(project_id=project))
                if prompt:
                    receipt = await app.submit_thread(thread_id=thread.thread_id, prompt=prompt)
                    operation = await app.wait_root_operation(receipt.receipt_id, timeout_seconds=30)
                    expected = "failed" if "[failure]" in prompt else "completed"
                    if operation.status != expected:
                        raise RuntimeError(f"Fixture {title!r} ended with {operation.status}, expected {expected}")
                if title == "A quick answer":
                    followup = await app.submit_thread(
                        thread_id=thread.thread_id, prompt="[answer] What should happen to the form after a failure?"
                    )
                    operation = await app.wait_root_operation(followup.receipt_id, timeout_seconds=30)
                    if operation.status != "completed":
                        raise RuntimeError("The follow-up fixture did not finish")
                print(f"  {title}: /threads/{thread.thread_id}", flush=True)
            question = await app.create_thread(
                title="Choose the review scope", defaults=NewThreadDefaults(project_id="project-research")
            )
            await app.submit_thread(
                thread_id=question.thread_id, prompt="[question] Help me choose what to review first."
            )
            print(f"  Choose the review scope: /threads/{question.thread_id}", flush=True)
            print(f"Review API: http://127.0.0.1:{port} (key: test-only-key)", flush=True)
            print(
                "All responses and tool results are scripted. Try [slow], [long], [thought], [retry], [answer], [failure], or [question].",
                flush=True,
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
