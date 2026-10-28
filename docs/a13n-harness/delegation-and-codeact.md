---
title: Delegation and CodeAct
description: Delegate to a declared child, continue its saved work, or combine eligible tool calls with restricted Python.
---

Use delegation when a task needs another Agent with its own instructions, Model, and tools. Use CodeAct when the current Agent needs Python control flow to combine tool results. Start with the offline examples below; each runs through the real Harness tool path without a provider account.

## Inline Delegation

Inline delegation waits for a declared child to finish and returns its output to the parent Model. Harness owns this nested execution; no scheduler is required. For work that must continue after the parent Run closes, use [asynchronous children](#asynchronous-children).

### Declare Child Topology

Use the Python environment from [Getting started](getting-started.md). Save this complete program as `delegation_demo.py`. The deterministic parent calls `delegate`, saves its State, then calls `resume_subagent` in a new Run. The child counts its own conversation turns so you can see continuation working.

```python title="delegation_demo.py"
import asyncio
import json
from collections.abc import AsyncIterator

from a13n_harness import (
    AgentDefinition, AgentSpec, HarnessBuilder, HarnessState, SubagentDefinition,
)
from a13n_harness.capabilities import SubagentCapability
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel


async def child_reply(
    messages: list[ModelMessage], info: AgentInfo,
) -> AsyncIterator[str]:
    del info
    turn = 1 + sum(isinstance(message, ModelResponse) for message in messages)
    yield f"reviewer-turn-{turn}"


def coordinator_model(resume: bool) -> FunctionModel:
    sent = False

    async def respond(
        messages: list[ModelMessage], info: AgentInfo,
    ) -> AsyncIterator[str | DeltaToolCalls]:
        nonlocal sent
        del info
        returns = [
            part for message in messages if isinstance(message, ModelRequest)
            for part in message.parts if isinstance(part, ToolReturnPart)
            and part.tool_name in {"delegate", "resume_subagent"}
        ]
        if not sent:
            sent = True
            if resume:
                previous = returns[-1].content
                assert isinstance(previous, dict)
                name = "resume_subagent"
                arguments = {
                    "execution_id": previous["execution_id"],
                    "prompt": "Check the revised plan.",
                }
            else:
                name = "delegate"
                arguments = {"subagent": "reviewer", "prompt": "Review this plan."}
            yield {0: DeltaToolCall(name=name, json_args=json.dumps(arguments))}
        else:
            result = returns[-1].content
            assert isinstance(result, dict)
            yield result["output"]

    return FunctionModel(stream_function=respond)


def build_parent(resume: bool):
    child = AgentDefinition(
        agent=AgentSpec(), output_type=str,
        definition_id="tutorial-reviewer-v1",
        model=FunctionModel(stream_function=child_reply),
    )
    return HarnessBuilder().build(
        AgentDefinition(
            agent=AgentSpec(), output_type=str,
            definition_id="tutorial-coordinator-v1",
            model=coordinator_model(resume),
            capabilities=(SubagentCapability(),),
            subagents=(SubagentDefinition(
                name="reviewer", description="Review one bounded plan.", agent=child,
            ),),
        )
    )


async def main() -> None:
    first = await build_parent(False).run("Review the plan")
    assert first.output_or_raise() == "reviewer-turn-1"
    assert first.state is not None
    print(first.output_or_raise())
    restored = HarnessState.model_validate_json(first.state.model_dump_json())
    second = await build_parent(True).run("Review the revision", previous_state=restored)
    assert second.output_or_raise() == "reviewer-turn-2"
    assert first.thread_id == second.thread_id and first.run_id != second.run_id
    print(second.output_or_raise())


if __name__ == "__main__":
    asyncio.run(main())
```

Run it:

```bash
uv run python delegation_demo.py
```

Expected output:

```text
reviewer-turn-1
reviewer-turn-2
```

Read the construction from the child outward:

1. `child` is a complete `AgentDefinition`, including its Model and business `output_type`.
2. `SubagentDefinition` gives that child a roster name and description under the parent.
3. `SubagentCapability()` exposes inline `delegate` and `resume_subagent` tools.
4. The parent Model chooses the child and supplies a bounded task. In this example the choice is scripted; a provider Model makes it from the instructions and available tools.

Keep stable definition IDs when rebuilding a compatible saved conversation. Build checks that the child graph is finite and acyclic and that sibling names are unique. Each built child is an immutable, reusable `ExecutableAgent` contained by its parent.

### Inline Execution and Continuation

`delegate(subagent, prompt)` starts a new nested conversation and returns a bounded result containing `execution_id`, `subagent`, and `output`. Use that exact ID with `resume_subagent(execution_id, prompt)` to continue the child. Calling `delegate` again starts another child conversation.

The example saves the parent's complete State as JSON and rebuilds the executable before resuming. That State includes the nested child's continuation. For persistence across process exits, store that JSON using the application's checkpoint workflow in [State and resume](state-and-resume.md).

During an inline call, Harness derives fresh child instance lineage, applies `SubagentDefinition.context`, and runs the child through `ExecutableAgent.stream()`. Child events keep their child Run/Thread attribution. Harness retains the complete child checkpoint before returning success to the parent.

The child borrows the parent's entered Environment mapping; it does not re-enter or close those adapters, publish their state separately, or change mounts and the default. The inherited invocation policy remains in force. A trusted `run_bindings_factory`, when supplied on the child declaration, can complete fresh bindings while preserving the required inherited identity, Environment, configuration, and policy. See [Environments](environments.md) for resource ownership.

Set limits on the child deliberately. Each child Run uses the strictest per-field intersection of its own definition limits and `SubagentDefinition.usage_limits`. It has its own usage accumulator, independent of the parent's budget. An async Host can narrow this ceiling. Aggregate attributed usage for display separately from enforcement.

Harness `0.6` changed the retained inline-child state format. Earlier states containing inline children fail resume or fork with `capability_state_version_unsupported`; start a new Thread for those conversations. States without inline children are unaffected, including states from Hosts using only async children.

### Asynchronous Children

Choose async mode when child work needs a Host-owned lifetime. Supply an implementation of `SubagentOperator`; this helper only wires an existing operator into the Capability:

```python
from a13n_harness.capabilities import SubagentCapability, SubagentOperator


def async_children(operator: SubagentOperator) -> SubagentCapability:
    return SubagentCapability(async_enabled=True, operator=operator)
```

Pass the returned Capability in the parent definition instead of `SubagentCapability()`. The operator must implement all six use cases before the Agent can use this mode:

| Model tool                              | Meaning of the result                                               |
| --------------------------------------- | ------------------------------------------------------------------- |
| `delegate(subagent_name, prompt)`       | Host admission, not child completion                                |
| `subagent_info(...)`                    | A fresh bounded view of Host execution records                      |
| `wait_subagent(...)`                    | One bounded wait or fan-in query; timeout does not cancel work      |
| `steer_subagent(execution_id, message)` | Acknowledgement of the steering request                             |
| `cancel_subagent(execution_id)`         | Acknowledgement of cancellation, not proof of cleanup               |
| `resume_subagent(execution_id, prompt)` | Admission of a linked continuation for a compatible resumable child |

Before admission, Harness resolves the declared child, derived Identity, context policy, usage ceiling, and detached parent correlation. It also supplies the originating tool-call ID and assembled name when available. The Host can use that correlation to define its own idempotency identity and replay scope.

`subagent_info` and `wait_subagent` query the operator on every call. Parent Run or Environment closure does not cancel accepted async work or close the operator. Inspect the Host's current execution record to determine the outcome.

### Deliberate Boundary

The Host implements the async lifecycle behind the operator:

1. Create the child Thread and construct fresh `RunBindings`, including its Environment association.
2. Invoke the exact child executable and retain its checkpoints and observed activity.
3. Route feedback, steering, cancellation, completion delivery, and resume.
4. Handle loss, retention, cleanup, and application shutdown.

Persist reconstructable definition references and detached correlation. Build fresh child authority on the selected worker; do not retain a live parent `AgentContext` or entered Environment as durable execution state. Inline execution is the built-in executor; async scheduling and storage belong to your application.

### Deferred Tools in Child Runs

Choose deferred support based on the child's feedback lifecycle. Roots and Host-managed children use fresh `RunBindings.deferred_tools_supported`, which defaults to `True`. Built-in inline children always disable it.

A Host without a child-feedback lifecycle sets this flag to `False`. Declaratively deferred tools and their first-party guidance are omitted. Runtime `CallDeferred` or `ApprovalRequired` becomes a `ToolDenied("Deferred tool interaction is unavailable for this Run.")` result in the current Model loop. Custom handlers cannot override this setting; unexpected terminal deferral fails with `deferred_tools_unsupported`. Harness UI selects this unsupported mode for async children while retaining ordinary prompt continuation.

#### Host-Managed Feedback

Inline `resume_subagent` continues a conversation with another prompt. It cannot submit deferred feedback or suspend the parent on behalf of the child, and a child bindings factory cannot enable that support.

For a Host-managed child that supports feedback, use the same [structured suspension flow](state-and-resume.md#structured-suspension) as a root:

1. Save the exact `HarnessRunResult.deferred` envelope with `result.state`.
2. Collect authenticated feedback for its target call IDs.
3. Invoke that child's `run()` or `stream()` directly with fresh bindings, `previous_state`, and `DeferredToolResume`.
4. Publish the selected checkpoint and deliver the result according to the Host lifecycle.

A parent Model call is not required for this resume. Observation events describe progress; use the actual checkpoint and pending request for continuation. Declare only the child's business `output_type`, including structured models. Harness adds deferred support itself and rejects an explicit reserved `DeferredToolRequests` business type. A suspended result has `deferred` and no business output; successful completion returns the declared type through `output_or_raise()`.

## CodeAct

CodeAct lets the current Agent use restricted Python to combine eligible tool calls. `run_code` accepts inline source; `run_program` reads a reusable `*.codeact.py` file through the current Environment. Both use Monty, a restricted Python runtime with supported system-time APIs and UTC as its default timezone. Filesystem, network, process, environment-variable, and credential access must go through the injected tools.

### Publish Eligible Tools

The tool owner explicitly allows CodeAct use with `CodeActPolicyToolset`. Save this second complete offline example as `codeact_demo.py`. It calculates a value, stores it, serializes State, and loads it in a newly built executable:

```python title="codeact_demo.py"
import asyncio
import json
from collections.abc import AsyncIterator

from a13n_harness import AgentSpec, HarnessBuilder, HarnessState
from a13n_harness.capabilities import CodeActCapability
from a13n_harness.toolsets import CodeActPolicyToolset, CodeActToolPolicy
from pydantic_ai.capabilities import Capability
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel
from pydantic_ai.toolsets import FunctionToolset


def double(value: int) -> int:
    """Return twice the supplied value."""
    return value * 2


math_tools = Capability(
    id="math-tools",
    toolsets=[CodeActPolicyToolset(
        wrapped=FunctionToolset([double], id="math-functions"),
        policy=CodeActToolPolicy(tools={"double": True}),
        reject_unknown_tools=True,
    )],
)


def build_calculator(code: str, expected: int):
    sent = False

    async def respond(
        messages: list[ModelMessage], info: AgentInfo,
    ) -> AsyncIterator[str | DeltaToolCalls]:
        nonlocal sent
        if not sent:
            sent = True
            assert "run_code" in {tool.name for tool in info.function_tools}
            yield {0: DeltaToolCall(name="run_code", json_args=json.dumps({"code": code}))}
        else:
            returned = [
                part for message in messages if isinstance(message, ModelRequest)
                for part in message.parts if isinstance(part, ToolReturnPart)
                and part.tool_name == "run_code"
            ]
            assert returned[-1].content == expected
            yield str(expected)

    return HarnessBuilder().build(
        AgentSpec(), model=FunctionModel(stream_function=respond), output_type=str,
        capabilities=(math_tools, CodeActCapability()),
    )


async def main() -> None:
    first_code = '''value = await double(value=4)
await store(key="math.result", value=value)
value'''
    first = await build_calculator(first_code, 8).run("Calculate and save")
    assert first.output_or_raise() == "8" and first.state is not None
    print(first.output_or_raise())

    restored = HarnessState.model_validate_json(first.state.model_dump_json())
    second_code = '''saved = await load(key="math.result")
await double(value=saved)'''
    second = await build_calculator(second_code, 16).run(
        "Use the saved result", previous_state=restored,
    )
    assert second.output_or_raise() == "16"
    assert first.thread_id == second.thread_id and first.run_id != second.run_id
    print(second.output_or_raise())


if __name__ == "__main__":
    asyncio.run(main())
```

Run `uv run python codeact_demo.py`. It prints `8`, then `16`. The first runner actually calls `double` and `store`; the new Run calls `load` and doubles the retained value. The Model asserts each native tool result before returning the answer.

`CodeActToolPolicy(tools={"double": True})` publishes the tool owner's permission to use this function from restricted Python. `CodeActCapability()` installs the runners and explicit state tools. Model visibility, metadata, or an apparently read-only name does not grant eligibility. The current prepared tool must still be available and pass ordinary permissions and authorization.

For eligible tools with `defer_loading=True`, discover the tool or load its deferred Capability outside the runner first. Their schemas enter the runner directory only while that availability evidence remains in the current context. If the evidence is removed, discover or load again. The proxy ToolSearch path below supports discovery within a runner.

### Nested Dispatch

Inside a runner, call injected tools with keyword arguments and await every call. Return the final expression from `run_code`, or the return value from `main` in a program. Keep only the result the Agent needs. The runner must be the only executable tool call in its Model response; coordinate multiple calls inside its code.

A nested call goes through the current final `ToolManager`, preserving argument validation, Capability hooks, managed policy, credentials, provider checks, events, and usage. A root's deferred handler can supply a nested result if it resolves before the call returns. A child denial or unresolved root request fails the current runner; there is no saved interpreter frame to resume later.

Source-validation errors, unavailable functions, sandbox errors, and resource-limit failures return failed tool results so the Agent can revise its code. They do not consume the runner's Model-retry budget. Outer argument-schema validation and runner-isolation violations use their normal retry policy.

Treat a failed program as potentially partial work. Monty resolves names as it executes, so a later unavailable call can fail after an earlier tool succeeded. Failure metadata records side-effect uncertainty after a nested call starts. Completed effects and explicit stored writes are not rolled back or automatically replayed; reconcile them before rerunning the program.

### Large Tool Collections with ToolSearch

Use [grouped ToolSearch](tool-search.md#use-with-codeact) when eagerly listing every schema would overwhelm the runner directory. In proxy mode, CodeAct gets search, description, and call functions. A program can discover, read the complete target schema and instructions, then invoke the exact target through the existing manager.

Publish the typed CodeAct policy on the source tools first. Proxy grouping changes discovery, not eligibility or authorization. Both runners support this path. Proxy calls remain sequential even inside `asyncio.gather`; ordinary independent eligible calls can run concurrently within the configured limits.

### Runtime State

Choose the state lifetime that matches the task:

| State                                       | Lifetime                                                                       |
| ------------------------------------------- | ------------------------------------------------------------------------------ |
| Ordinary `run_code` variables and functions | Current logical Harness Run; successful feeds can reuse them                   |
| `run_program` variables                     | One invocation in a fresh interpreter                                          |
| Values saved with `store`                   | Explicit Capability state; crosses Runs when the Host saves and restores State |

`run_code(restart=True)` clears the inline interpreter before evaluation. An unsuccessful feed also clears that interpreter. Neither operation deletes successful explicit `store` writes. Ordinary interpreter values are not part of `HarnessState`.

For a reusable program, save this UTF-8 file in a readable Environment mount. Keep the same eligible `double` tool and CodeAct Capability from the example:

```python title="double.codeact.py"
async def main(inputs):
    result = await double(value=inputs["value"])
    await store(key="math.result", value=result)
    return result
```

With that file at `/workspace/double.codeact.py`, the Model can call `run_program` with:

```json
{"path": "/workspace/double.codeact.py", "inputs": {"value": 6}}
```

The result is `12`, also saved under `math.result`. The Host must bind the readable Environment source; use [Environments](environments.md) for that setup. This file access uses the Host's Environment interface and does not require exposing file tools to the Model.

Every program invocation reads the source again and starts a fresh interpreter. The file must end in `.codeact.py` and define exactly one undecorated `async def main(inputs)`. Module scope permits supported imports, declarations, docstrings, and side-effect-free constants. Put execution inside `main`. Inputs are JSON data, and the path grants no additional tool eligibility.

To lower the execution budget, replace `CodeActCapability()` in the builder with a configured instance:

```python
from a13n_harness.capabilities import CodeActCapability, CodeActConfig

codeact = CodeActCapability(CodeActConfig(
    max_tool_calls=16,
    max_concurrency=4,
    timeout_seconds=60,
    max_state_entries=64,
    max_state_bytes=1024 * 1024,
))
```

The default per-runner limits are 128 nested tool attempts, concurrency 16, and a 300-second deadline. Parent tool-call limits and provider ceilings still apply. A Host that retains runner execution must keep its runtime and explicit state owner; see [Host embedding](hosting.md). Receipt delivery ends the foreground wait, not the runner or its nested effects.

### Save data explicitly, not the interpreter

Use `store` for JSON data needed after a feed or Run ends. These snippets are runner payloads, not ordinary standalone Python modules:

```python
await store(key="search.results", value={"ids": [12, 34], "next_page": 3})
```

Read the stored value in a later runner:

```python
results = await load(key="search.results")
results["ids"]
```

`load()` lists all keys; `forget(key="search.results")` deletes one key and returns whether it existed. There is no message or description argument. A missing key fails; stored null loads as `None`. Loads return detached data, so mutate and call `store` again to save changes.

The complete example above shows the cross-Run requirement: export and restore `HarnessState`. A successful write survives a subsequent sandbox failure or reset, but a process crash before Host persistence can lose it. Newly created children have separate state. Forked State is a detached copy, not a shared mapping, and restoring values does not restore tool authority.

The Model receives a key-only directory of at most 32 keys and 4 KiB. Call `load()` to list omitted keys. `CodeActConfig.max_state_entries` defaults to 256; `max_state_bytes` defaults to 10 MiB for the full compact JSON namespace, including keys. Keys contain 1–256 characters and values must be finite JSON. A rejected write preserves the previous value. Restored state must also fit the current limits.

## Choosing Between Them

| Task                                                                 | Use                                      |
| -------------------------------------------------------------------- | ---------------------------------------- |
| Give another Agent a bounded task and wait for its answer            | Inline delegation                        |
| Combine eligible tools with Python loops, conditions, or concurrency | CodeAct                                  |
| Keep child work alive beyond the parent Run or process               | A Host-owned async child or task service |
| Execute trusted application Python with its normal libraries         | Ordinary application code                |
| Run untrusted shell commands or native programs                      | An isolated Environment provider         |

Add either feature when that execution pattern helps the application. A direct tool call remains the simpler path for a single operation.
