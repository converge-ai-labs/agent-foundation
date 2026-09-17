# Editing, conflicts and evidence

Submit at most 32 ordered operations. Paths are arrays of strings relative to config. `set` changes one field or replaces a complete object or array; an empty path initializes or replaces the complete configuration. Explicitly retain or clear existing fields during full replacement. `remove` removes an optional field or mapping entry, never the root or a required field. `replace_text` requires one exact nonempty old string occurring exactly once. Arrays have no index edits.

The complete candidate and all resource bindings must validate before any edit is saved. A failed command leaves the prior draft and validation unchanged. A stale expected version requires rereading, not forcing a write. Repeated logical tool calls retain the host's idempotency identity.

Apply conflicts mean the business target changed. Preserve the candidate and ask the user to review an explicit merge and rebase. Rebase is a human operation. The original source remains immutable even when the merge base changes.

Use actual Run evidence only. Keep scenario input, expectations and resource limits fixed before execution. A Run must match the candidate version, digest, effective configuration and dependency observations. Changed candidates or dependencies make evidence stale. Never rewrite expected results after observing an output, count another Run's usage twice, or claim an unsupported test passed.

Application leaves the Session's shared draft open. The host advances its version and updates its target and base to the applied Agent Revision, while retaining the original source and immutable application receipt. All Threads and old Runs remain bound to that same draft. Read its current version, target, base and latest receipt before editing; an older Run's initial draft version is not a current write precondition. Discarded and expired drafts are terminal. Start a new Session for an independent candidate.

For `fields` errors, `field_index` identifies the zero-based selector entry and `segment_index` identifies the failing key within that path. Correct that entry or read its safe parent; select arrays whole. For `invalid_cursor`, omit the cursor to restart pagination and use only the returned next cursor with the same query and scope. `read_interaction_run` includes `failure` for a failed Run; use its safe code and retry hint instead of inferring the cause from an empty output.
