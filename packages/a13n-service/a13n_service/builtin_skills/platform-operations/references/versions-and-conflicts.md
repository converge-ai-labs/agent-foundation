# Versions, review and conflicts

Read -> prepare the exact change -> obtain review if required -> write with the original ETag -> inspect the result. Pass `headers.etag` as the tool's `expected_version` argument; the version without quotes is also accepted. A list resource's ETag is `"id:version"` (or `"key:version"` for a Model). Resource version and immutable Revision ID are different concepts.

A 412 conflict means state changed after the read. Read again, compare, preserve new unrelated fields, and review the revised request. Never silently substitute the latest version into an already prepared write. A 428 means the precondition is missing: read the resource and prepare a new call.

Use the original API's replace/null/omission semantics. Full AgentConfig revisions preserve fields not being changed. Tool approval is not idempotency. Do not replay writes after a timeout or lost response; query state first and report uncertainty if success cannot be established. Multiple resource writes are not a transaction. Report successful IDs and remaining work without deleting completed resources as an invented rollback.
