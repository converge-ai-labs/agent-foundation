# Visible history

> Discussion proposal, not the current specification. See [status and scope](README.md).

## Items

Visible items keep today's model: identity, kind, state, stream positions, timestamps, and bounded content ([`display.py`](../../../packages/a13n-service/a13n_service/runs/display.py)). Each item also receives a dense per-Run `ordinal` when it first appears.

Per-field limits stay: 262144 characters per message text, tool arguments, or tool result. The whole-Display limits of 4096 items and `worker.display_bytes` no longer discard history.

## Pages and the tail

Display becomes two kinds of objects:

- **History pages.** Immutable objects of finished items in ordinal order, written once and kept permanently.
- **The tail.** The items not yet in a page. It takes the place of today's Display object: the worker writes it with each checkpoint, and the checkpoint transaction moves the Run's display pointer to it.

When the oldest items of the tail form a full page of finished (`completed` or `failed`) items, initially 256 items or 1 MiB, the worker writes them as a page in the same publication. The checkpoint transaction records the page and the new tail together, so every item is in exactly one place.

Unfinished items (`in_progress` and `interrupted`) never enter a page while the Run executes, because a later attempt may still continue an interrupted item, as today. Pages hold only items that can no longer change, so a written page never changes.

The tail stays small. The Service commits only at the primary Run's boundaries, and an inline child's items are committed when its delegation returns. At any checkpoint, the only unfinished items are therefore the tool calls of the latest model response. The tail holds those, plus less than one page of finished items.

## Sealed Runs

A Run's tail freezes when it seals. A worker sealing a failed or cancelled Run may first publish a tail with its unfinished items marked `interrupted`, as today; other seals write nothing. Readers show any unfinished item of a sealed Run as `interrupted` ([`runs.py`](../../../packages/a13n-service/a13n_service/runs/runs.py)). A waiting Run's approval-pending tool call stays in its tail.

## Reads

`GET …/runs/{run}/items` pages by ordinal with `before`, `after`, and `limit`. By default it returns the newest items: the tail and, to fill the limit, the end of the last page. It keeps `position`, `resume_after`, and `complete`; `dropped` disappears.

A read takes the display pointer and the page catalog in one short snapshot, then fetches the tail and only the pages it needs. Every item that a later live event can change is in the tail, so a client that applies the live stream from the returned position always has the item an event names.

The live thread stream is unchanged. Console loads the newest items, applies live output from their position, and loads earlier pages on demand.

## Ownership

This proposal owns the items API change and Console paging, on today's item model. #788 proposed a shared display projector; its implementation PR #815 was closed without merging, and no replacement is open. #788 is not a prerequisite. If it is picked up again, it builds on this paged storage.
