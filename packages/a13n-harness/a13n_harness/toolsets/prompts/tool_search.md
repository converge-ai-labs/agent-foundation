## Grouped tool discovery

Use `{search_name}` to discover operations and `{call_name}` to execute them. The group summaries describe available domains; they are not a list of every operation.

1. Search using operation keywords. Select a group when its purpose matches the task; omit `group` when unsure. Use `query=""` to browse a group. Follow `next_offset` with the same query and group to see another page; offsets are only valid for the current tool directory.
2. Read the returned `parameters_json_schema` and `instructions`. Copy the exact `group` and `tool` values, supply required fields, and respect enum values, nested objects, and omission versus null. A discovered name is not a top-level callable.
3. Call `{call_name}(group=..., tool=..., arguments=...)`. Search never executes an operation; call can change external state. Reuse a schema already discovered in the current run when it remains valid rather than searching before every call.
4. On unknown tools or schema errors, search again and correct the arguments. On timeout, cancellation, or uncertain execution failure, inspect the outcome before retrying a mutation. Proxy calls are not transactions and do not roll back earlier effects.

When CodeAct is present, await the same proxy search/call functions with keyword arguments. Only results marked `codeact_eligible=true` may be executed inside CodeAct; grouping never grants that permission. Do not call a discovered business name as a Python function. Select useful result fields instead of returning an entire large catalog. A program may discover and call an operation in one feed, but must still understand and follow the returned schema. Proxy calls execute sequentially; do not assume `asyncio.gather` makes them parallel.
