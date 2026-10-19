# Execution and diagnostics

Start with the IDs selected in Console. Without IDs, list Runs by Agent, status, principal or creation time. Time ranges are UTC-aware, include created_after and exclude created_before. Lists are newest first and paginated; keep the same filters while following next_cursor. Session, Thread and Run IDs are distinct.

Read a Run to obtain its Session, Thread, frozen Agent Revision, status and current Attempt. Query the Thread inbox for submitted, pending or withdrawn input, and Run items and saved content for the displayed request and response. Read every relevant Attempt: a failed recovery attempt does not imply that the final Run failed. Attempt detail and trace operations require both the Run and Attempt IDs. Trace evidence can be incomplete, expired or unavailable. Findings are hypotheses with evidence; inspect the cited execution before proposing a change.

Run failure messages retain endpoint-policy reasons and model HTTP status codes. A `provider_endpoint_denied` failure needs a changed endpoint or deployment policy; repeating the same configuration does not help. An HTTP status identifies the upstream response class, not the exact cause; inspect the Provider's authentication and endpoint settings before attributing it to a bad key.

Use only these read operations for diagnosis. Do not start, interrupt, fork or resume executions. Explain evidence limits and partial results; absence of retained trace data is not proof of correct execution.

Use `view=compact` on Run, thread Run, lineage, inbox, item, content and trace queries. A Run's `section` selects input, output, failure, pending or other named fields. `/runs/{run_id}/agent` selects `section=original|effective|overrides`; `part` selects a configuration field within original/effective. Compact Run configuration reports changed top-level field paths and bounded resource references.

Item reads accept `kind`, `state`, `tool_name`, and exclusive `before`/`after` ordinals. Compact and filtered reads scan a strict bounded window (compact starts at the oldest ordinal unless a position is given), without attaching the full tail. Follow the returned directional continuation even when no matching item was found. Read `/runs/{run_id}/items/{item_id}/content` with that item's `ordinal` and `field=text|arguments|result|metadata|all`. Saved content reads use `offset`, `limit`, `version` and optional literal `query`; a field reference is resolved under the same Run's authorization.

Trace lists accept `status`, `kind` and `tool_name`. Read a root's named section directly. For a selected span use `/traces/{trace_id}/spans/{span_id}/content`; it searches one bounded backend page at a time. If `items` is empty, follow `next_cursor`. Keep the cursor that located the span for later fragments of that same span. Trace search remains subject to retention and the configured backend's availability.
