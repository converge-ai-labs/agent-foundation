# Agent Application Example

This project is one small, complete application: an offline streaming chat that keeps one conversation across multiple turns, persists `HarnessState`, and resumes the same Thread after the process is restarted.

It intentionally uses only:

- one offline Pydantic AI `FunctionModel`;
- one local demo Environment;
- one state file;
- one `ConversationApplication` execution path.

Environment permutations and advanced ownership combinations belong in the Harness Environment tests and documentation, not in this application example.

## Run It

From the repository root:

```bash
make examples-check-all
```

Or run the application directly:

```bash
cd examples/agent-app
uv sync --locked
uv run agent-app-example
```

Enter several messages and use `/quit` to stop. The default state file is `.agent-app/conversation-state.json`; the demo Environment workspace is `.agent-app/workspace`.

Start the command again and the next turn resumes the same Harness Thread and message history.

The same lifecycle can be demonstrated non-interactively:

```bash
uv run agent-app-example \
  --state ./conversation-state.json \
  "Remember that the release is Friday." \
  "When is the release?"

uv run agent-app-example \
  --state ./conversation-state.json \
  "Continue after the application restart."
```

Use `--workspace PATH` only when you want the single demo Environment rooted somewhere else.

## Application Flow

```mermaid
sequenceDiagram
    participant User
    participant App as ConversationApplication
    participant State as HarnessState file
    participant Provider as Demo Environment Provider
    participant Environment as Fresh Environment
    participant Harness

    User->>App: first prompt
    App->>State: load state or start a new Thread
    App->>Provider: construct fresh Environment
    App->>Harness: stream prompt with previous_state and Environment
    Harness->>Environment: enter
    Harness-->>App: text events and terminal result
    Harness->>Environment: non-destructive close
    App->>State: atomically commit completed state

    User->>App: next prompt
    App->>State: load previous completed state
    App->>Harness: stream next turn
    Harness-->>App: continued response and next state
    App->>State: atomically commit completed state

    Note over App,State: A new process constructs a new application and loads the same state file
```

The Provider, Environment adapter, credentials, and runtime collaborators are fresh authority and are not serialized into `HarnessState`. A checkpoint can contain portable provider-defined Environment state, but the Host must select that state and construct a fresh compatible Environment before a later independent Run. This Direct Local example is stateless. Conversation history resumes because the application reloads the last completed state and passes it as `previous_state`.

## Main API

```python
from pathlib import Path

from a13n_agent_app_example import (
    ConversationApplication,
    create_demo_environment,
)

state_path = Path("conversation-state.json")
workspace = Path("workspace")

application = ConversationApplication(
    model=model,
    state_path=state_path,
    environment_factory=lambda: create_demo_environment(workspace),
)
async with application.stream_turn("Hello") as stream:
    async for text in stream:
        print(text, end="", flush=True)
```

`ConversationApplication` caches one reusable `ExecutableAgent` for its process lifetime. For each turn it:

1. serializes turn execution with an async lock;
2. loads the last successfully committed `HarnessState` if present;
3. constructs one fresh Direct Local Environment and opens one explicitly scoped turn stream with `previous_state`;
4. yields text start and delta events immediately;
5. closes the Harness stream and Environment adapter non-destructively if the consumer stops early;
6. requires a successful terminal result;
7. atomically replaces the state file with the returned continuation state.

The executable is immutable build output and has no independent resource lifecycle. The application constructs one fresh adapter per turn; Harness enters it, uses it for Run-local routing, and closes it without deleting the Host workspace. Harness never calls Provider destruction.

A failed or abandoned turn does not replace the last completed state. Callers must enter `stream_turn()` with `async with`; leaving that scope deterministically closes the Harness stream and fresh Environment adapter. The next application instance can therefore recover only from a committed checkpoint.

## Source Layout

```text
src/a13n_agent_app_example/
  application.py  # Multi-turn streaming and HarnessState persistence
  environment.py  # The single offline demo Environment
  cli.py          # Interactive and non-interactive entry point

tests/
  test_application.py  # Multi-turn chat, restart recovery, failure, and cleanup
```

## Using a Real Model

The runnable CLI uses an offline `FunctionModel`, but `ConversationApplication` accepts any native Pydantic AI `Model`:

```python
from a13n_harness import infer_model

model = infer_model(
    "openai-responses:gpt-5",
    provider_factory=provider_factory,
    patches=(apply_provider_patch,),
)
```

The caller owns model credentials, SDK clients, retry policy, and client cleanup.

## Boundaries

- `HarnessState` is portable conversation continuation data, not live Environment authority.
- The state file is a small single-process example, not a distributed checkpoint store or lease protocol.
- The local demo Environment is intentionally the only Environment in this application.
- Advanced multi-Environment routing and mixed ownership remain documented in the [Environment guide](../../docs/agent-harness/environments.md).
- Production Host lifecycle and checkpoint authority are documented in [Embedding in a Host](../../docs/agent-harness/hosting.md).
