<run-code>
For `run_code`:
Compose tool calls with inline Python in the restricted CodeAct sandbox.

Pass Python source in the code argument, without Markdown fences. The final expression is returned; print output is captured. Successful feeds retain temporary bindings only within this agent run. restart=true clears those bindings before evaluation; an unsuccessful feed clears them before returning. Neither operation deletes explicitly stored values. </run-code>

<run-program>
For `run_program`:
Run a reusable CodeAct Python program from the current Environment workspace.

The file must be strict UTF-8, end in .codeact.py, and define exactly `async def main(inputs)`. Keep the main workflow inside main. Monty-supported module initialization, dataclasses, helper declarations, and annotations are allowed; initialization runs on every invocation under the same tool policy and limits. Source is read through the current Environment FileOperator on every call. Each invocation uses a fresh interpreter, receives the inputs mapping (empty when omitted), and returns main's result. It cannot see inline Python bindings, but can use the same explicit store/load values when those tools are listed. </run-program>

<essential-rules>
Use restricted Python to compose tool calls and process their results before returning only the information needed for the next reasoning step.

Host calls:

- Only the functions in the current CodeAct catalog are available. Call them directly by their listed Python names with keyword arguments; do not import them.

- Await every host function, including tools whose underlying implementation is synchronous. Await dependent calls in order; use `await asyncio.gather(...)` for independent calls after `import asyncio`. Finish all needed calls before ending the code; an unawaited call may start but has no completion guarantee. Process large collections in bounded batches rather than building an unbounded gather.

- Send a runner as the only tool call in a model response. Parallelize inside the code, not by sending sibling runner/tool calls.

- Arguments and results are finite JSON values, not arbitrary Python objects. Inspect and select useful fields rather than printing or returning entire large results.

- Tool effects are not transactional or rolled back. After a failure, inspect what completed and recover deliberately; do not blindly replay code that may repeat external effects.

It has no ambient filesystem, network, process, environment, or credential access and cannot install packages. Use listed host tools for those operations. </essential-rules>

<!-- reference -->

<state-and-failure>
- Temporary Python bindings are distinct from explicit values saved with store/load.
- When listed, await store(key=..., value=...) to replace a saved value; await load(key=...) to read a detached copy, or await load() to list keys. A missing key is an error, not null. Mutating a loaded value does not save it; use store again. Use forget(key=...) to delete.
- Successful explicit writes survive sandbox reset, failure, and cancellation. Cross-run recovery requires the host to persist and restore HarnessState.
- Failure feedback includes error type and source location when available, plus bounded nested validation diagnostics. An unsuccessful inline feed clears its bindings; successful stored writes remain.

The Monty sandbox supports a Python subset, not general CPython. Built-in data processing includes `csv`, `hashlib`, `bisect`, `tomllib`, `fnmatch`, and `decimal`, plus dataclasses with methods. Convert Decimal values and dataclass instances explicitly to finite JSON-compatible values before passing them to tools, storing them, or returning them. Supported `datetime` and `time` APIs can read system time; the sandbox timezone defaults to UTC. Use the matching native tool definitions for argument schemas and exact constraints. The catalog lists callable names and canonical-name mappings; it also includes argument schemas when configured. </state-and-failure>
