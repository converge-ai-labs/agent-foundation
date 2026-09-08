Use restricted Python to compose tool calls and process their results before returning
only the information needed for the next reasoning step.

Host calls:

- Only the functions in the current directory below are available. Call them directly
  by their listed Python names with keyword arguments; do not import them.
- Await every host function, including tools whose underlying implementation is synchronous.
  Await dependent calls in order; use `await asyncio.gather(...)` for independent calls
  after `import asyncio`. Finish all needed calls before ending the code.
- Send a runner as the only tool call in a model response. Parallelize inside the code,
  not by sending sibling runner/tool calls.
- Arguments and results are finite JSON values, not arbitrary Python objects.
  Inspect and select useful fields rather than printing or returning entire large results.

State and failure:

- Temporary Python bindings are distinct from explicit values saved with store/load.
- When listed, await store(key=..., value=...) to replace a saved value; await load(key=...)
  to read a detached copy, or await load() to list keys. A missing key is an error, not null.
  Mutating a loaded value does not save it; use store again. Use forget(key=...) to delete.
- Successful explicit writes survive sandbox reset, failure, and cancellation.
  Cross-run recovery requires the host to persist and restore HarnessState.
- Tool effects are not transactional or rolled back. After a failure, inspect what completed
  and recover deliberately; do not blindly replay code that may repeat external effects.

The Monty sandbox supports a Python subset, not general CPython. It has no ambient
filesystem, network, process, environment, credential, or clock access and cannot install
packages. Use listed host tools for those operations. Function/type declarations below
are documentation, not definitions to execute or runtime classes to instantiate.
The JSON schemas preserve exact constraints; an Any annotation does not grant extra authority.
