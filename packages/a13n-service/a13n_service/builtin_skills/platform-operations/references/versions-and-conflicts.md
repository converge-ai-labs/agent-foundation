# Versions, review and conflicts

Read the target, prepare the exact change, obtain review if required, then write with the original version and inspect the business result. Resource reads include `id` and `version`; send `id:version` as `expected_version`. Models use `key:version`. Quotes are accepted. Resource version and immutable Revision ID are different concepts. For a configuration-change result, the returned Agent head carries its resulting version.

Conditional tools require `expected_version`. `precondition_failed` means the resource changed after the read. Read again, compare and review a revised delta; never silently substitute the latest version into an approved call. Missing required arguments are rejected by the tool schema. Approval binds the target, exact arguments and base version and cannot widen business authority.

Omitted delta fields preserve saved values. Explicit null clears only fields whose schema allows it. Supplied collections and documented structured sections replace those values whole. Related Agent fields publish atomically; unrelated resource writes are separate transactions.

Approval is not idempotency. Never automatically replay a write after a timeout or lost response. Query state first and report uncertainty if success cannot be established. Where a tool declares an idempotency key, keep the same key for the same intent; a new key can duplicate work. Report successful resource IDs and remaining work without deleting completed resources as an invented rollback.
