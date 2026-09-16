# Editing, conflicts and evidence

Submit at most 32 ordered operations. Paths are arrays of strings relative to config. `set` changes one field or replaces a complete object or array; an empty path initializes or replaces the complete configuration. Explicitly retain or clear existing fields during full replacement. `remove` removes an optional field or mapping entry, never the root or a required field. `replace_text` requires one exact nonempty old string occurring exactly once. Arrays have no index edits.

The complete candidate and all resource bindings must validate before any edit is saved. A failed command leaves the prior draft and validation unchanged. A stale expected version requires rereading, not forcing a write. Repeated logical tool calls retain the host's idempotency identity.

Apply conflicts mean the business target changed. Preserve the candidate and ask the user to review an explicit merge and rebase. Rebase is a human operation. The original source remains immutable even when the merge base changes.

Use actual Run evidence only. Keep scenario input, expectations and resource limits fixed before execution. A Run must match the candidate version, digest, effective configuration and dependency observations. Changed candidates or dependencies make evidence stale. Never rewrite expected results after observing an output, count another Run's usage twice, or claim an unsupported test passed.

Applied, discarded and expired drafts are terminal. Old Runs remain bound to their original draft. On new ordinary input after apply, the host supplies a successor draft and the previous receipt; read that new draft before editing. An answer to an older pending question retains the older binding and grants no permission to apply or edit a successor.
