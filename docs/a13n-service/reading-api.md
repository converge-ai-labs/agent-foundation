---
title: Read large API results
description: Read compact summaries, selected sections and versioned fragments without losing continuation.
---

Use [HTTP conventions](http.md) for authentication and a [conversation walkthrough](connect-application.md) for your first integration.

## Read summaries and selected details

For large histories, find the record first, then fetch the field you need. For example:

```http
GET /api/v1/runs?status=failed&limit=10
GET /api/v1/runs/{run_id}?section=failure
GET /api/v1/runs/{run_id}/items?view=compact&kind=tool_call&state=failed
GET /api/v1/runs/{run_id}/items/{item_id}/content?ordinal={ordinal}&field=result&offset=0
```

Substitute the returned run ID, item ID and that item's actual `ordinal`. Compact item reads begin at the oldest ordinal unless you supply `before` or `after`; follow `next_before` or `next_after`, including after an empty filtered page. Detailed unfiltered reads select the latest items and full tail.

Run lists and lineage return metadata and `input_preview: {text, truncated}`, with at most 200 Unicode code points. They do not accept `view`. Read `/runs/{run_id}` for full details. Thread run pages additionally support `anchor=<run_id>` instead of `cursor`, report `total` and each item's chronological `number` (starting at one), and supply cursors toward older (`next_cursor`) and newer (`previous_cursor`) runs. Usage summaries already include each run's `run_number`.

Revisions, models, inbox entries, display items, spans and single-run detail support `view=compact`; omission keeps `view=detailed`. Compact lists default to ten records. Both compact lists and run discovery pages can stop early at the shared 10,000-character response budget. Follow their continuation values rather than assuming `limit` records will arrive.

Read the response metadata before treating a preview as complete:

- `truncated` marks a shortened preview; `omitted_sections` identifies fields available separately.
- `source_truncated` marks content shortened before storage. Further reads cannot recover the discarded part.
- `representation: fragment` supplies `text`, a content `version`, `next_offset` and `has_more`. Continue with both the returned offset and version; offsets count Unicode code points. On `409 conflict` with reason `content_changed`, restart the read. Join JSON fragments before parsing the whole value.
- Literal `query` searches return a match and its context. Their next offset continues the search, not a full extraction.

Choose the detail route for the task:

| Task                     | Read path                                                                                                                                                                                                                                                                                                                                                   |
| ------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Edit configuration       | Read a revision's `section=all` or a field such as `instructions`. Run `/agent` accepts section `original`, `effective` or `overrides`, with optional `part` for original/effective. Revision `/compare` takes `base_revision_id` and optional section. Obtain the complete original configuration and head ETag before editing; preserve unrelated fields. |
| Inspect tool definitions | Search `/toolsets/index`, `/connections/{connection_id}/tools/index` or `/connector-providers/{provider_id}/apps/{app}/actions/index`. Call the matching `/definitions` with up to five repeated exact `name` values. Check `missing` and `oversized`; retrieve an oversized definition by one name and versioned fragments.                                |
| Inspect a model          | Use model-catalog compact search, capability filters and paging, then select the exact provider/model for characteristics or pricing.                                                                                                                                                                                                                       |
| Inspect a trace span     | Filter trace lists by `status`, `kind` or `tool_name`. Read `/traces/{trace_id}/spans/{span_id}/content?section=input` or `section=output`, continuing fragments with version and offset.                                                                                                                                                                   |

Trace span content queries fetch that span alone; empty `items` means it is absent from the trace. A span exceeding the backend's 8 MiB response limit returns `413 payload_too_large`. Available trace data depends on backend availability and retention. Use complete configuration and definitions for writes, rather than submitting a summary, fragment or diff as the whole value.
