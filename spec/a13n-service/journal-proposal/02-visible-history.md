# Visible history

> Discussion proposal, not the current specification. See [status and scope](README.md).

## Items

Visible items keep today's model: identity, kind, state, stream positions, timestamps, and bounded content ([`display.py`](../../../packages/a13n-service/a13n_service/runs/display.py)). Each item also receives a dense per-Run `ordinal` when it first appears.

Per-field limits stay: 262144 characters per message text, tool arguments, or tool result. The whole-Display limits of 4096 items and `worker.display_bytes` no longer discard history.

## Pages and the tail

Display becomes two kinds of objects:

- **History pages.** Immutable objects of final items in ordinal order, written once and kept permanently.
- **The tail.** The items not yet in a page. It takes the place of today's Display object: the worker writes it with each checkpoint, and the checkpoint transaction moves the Run's display pointer to it.

When the oldest items of the tail form a full page of final items, initially 256 items or 1 MiB, the worker writes them as a page in the same publication. The checkpoint transaction records the page and the new tail together, so every item is in exactly one place.

## Final items

An item is final when no later event of the Run can change it. At each checkpoint, only the tool calls of the primary Run's latest model response that have no result in the exported state stay open; a takeover continues those in place, as today. Every other item is final. `completed` and `failed` items are already final, and the worker marks every other unfinished item `interrupted` in the same snapshot. Pages hold only final items, so a written page never changes.

The rule follows from how the Service checkpoints:

- The Service commits only at the primary Run's boundaries. A tool boundary commits before its batch runs, and the next model boundary commits after the batch ends, so no inline child is running at a checkpoint.
- Every `delegate` or `resume_subagent` call starts a new child run, and item identity includes that run's `subagentRunId`, so no later event reaches an earlier child's items.
- Earlier model responses already have their results in the exported state.

For example, an inline child that fails after starting a tool call leaves that call `in_progress` with no result event. No later event can finish it, so the next checkpoint marks it `interrupted`, and it pages normally instead of holding every later item in the tail.

The tail therefore holds at most the open tool calls and less than one page of final items.

## Sealed Runs

A Run's tail freezes when it seals. A worker sealing a failed or cancelled Run may first publish a tail with its unfinished items marked `interrupted`, as today; other seals write nothing. Readers show any unfinished item of a sealed Run as `interrupted` ([`runs.py`](../../../packages/a13n-service/a13n_service/runs/runs.py)). A waiting Run's approval-pending tool call stays in its tail.

## Reads

`GET …/runs/{run}/items` pages by ordinal with `before`, `after`, and `limit`. By default it returns the newest items: the tail and, to fill the limit, the end of the last page. It keeps `position`, `resume_after`, and `complete`; `dropped` disappears.

A read takes the display pointer and the page catalog in one short snapshot, then fetches the tail and only the pages it needs. Every item that a later live event can change is in the tail, so a client that applies the live stream from the returned position always has the item an event names.

The live thread stream is unchanged. Console loads the newest items, applies live output from their position, and loads earlier pages on demand.

## Ownership

This proposal owns the items API change and Console paging, on today's item model. #788 proposed a shared display projector; its implementation PR #815 was closed without merging, and no replacement is open. #788 is not a prerequisite. If it is picked up again, it builds on this paged storage.
