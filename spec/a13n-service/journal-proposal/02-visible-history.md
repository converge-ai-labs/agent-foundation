# Visible history

> Discussion proposal, not the current specification. See [status and scope](README.md).

## Items

Visible items keep today's model: identity, kind, state, stream positions, timestamps, and bounded content ([`display.py`](../../../packages/a13n-service/a13n_service/runs/display.py)). Each item also receives a dense per-Run `ordinal` when it first appears.

Per-field limits stay: 262144 characters per message text, tool arguments, or tool result. The whole-Display limits of 4096 items and `worker.display_bytes` no longer discard history.

## Staging

At each boundary, the items changed since the previous boundary are upserted into `run_items` in the same fenced transaction as the [staged state](01-running-state.md#start-object-and-staged-changes), so the visible history still covers exactly the committed state. The stream position and Redis resume hint, today stored with the Display pointer, move to a Run column.

A failed or cancelled seal marks the Run's unfinished items `interrupted` in its own transaction. No Display object is uploaded.

## Finished items never change

`completed`, `failed`, and `interrupted` items are final. When a later attempt continues an interrupted tool call, it appends a new item instead of reopening the old one. A written history page therefore never changes.

## History pages

The [background job](03-storage-and-apis.md#background-job) moves finished items from PostgreSQL to object storage:

1. When a Run has accumulated a page of finished items (initially 256 items or 1 MiB), or when it ends, the job reads the next contiguous finished items by ordinal.
1. It uploads them as one immutable page object.
1. One transaction records the page in `run_item_pages` and deletes the corresponding `run_items` rows.

Because the page record and the row deletion share one transaction, every item is in exactly one place: staged in PostgreSQL or in one page. PostgreSQL holds only recent items of executing Runs. Pages are kept permanently; like other history today, they have no deletion policy.

## Reads

`GET …/runs/{run}/items` returns the newest page by default and pages backward or forward by ordinal with `before`, `after`, and `limit`. It keeps `position`, `resume_after`, and `complete`; `dropped` disappears. A read takes one short snapshot of the page catalog and the staged rows for the requested range, then fetches only the pages it needs.

The live thread stream is unchanged. Console loads the newest page, applies live output from its position, and loads earlier pages on demand.

## Relationship to #815

#815 owns the shared display projector, typed live deltas, and the browser applicator. This proposal owns how the Service persists and pages visible items. The items API should change once, coordinated with #815, so SDK consumers see a single breaking revision.
