# Storage and APIs

> Discussion proposal, not the current specification. See [status and scope](README.md).

These are logical schemas, not generated DDL. Every table follows the existing tenant integrity rules, and migrations are generated with the owning Make target against a disposable database.

## PostgreSQL

```text
run_item_pages            # permanent history page catalog
  run_id, first_ordinal, last_ordinal, item_count, key, digest, size, format
  PRIMARY KEY (run_id, first_ordinal)
```

Changes to existing rows:

- `runs.checkpoint` keeps its meaning: the Run's latest complete state object. It also records the compression format and `refs`, the content and subagent objects of this Run that the state references, for cleanup.
- `runs.display` keeps its meaning, but now names the tail object rather than a complete Display.
- `threads.head_run_id` may name any sealed Run, and its database check changes accordingly ([continuation](01-state-and-continuation.md#continuing-after-a-failed-or-cancelled-run)).

There are no staging tables. A checkpoint transaction moves the two pointers and inserts any new page rows, as today's checkpoint moves the state and Display pointers.

## Object store

| Key                                                  | Written by                             | Kept                                       |
| ---------------------------------------------------- | -------------------------------------- | ------------------------------------------ |
| `orgs/{org}/runs/{run}/state/{attempt}/{random}`     | Checkpoints                            | Final object kept; replaced ones reclaimed |
| `orgs/{org}/runs/{run}/tail/{attempt}/{random}`      | Checkpoints                            | Final object kept; replaced ones reclaimed |
| `orgs/{org}/runs/{run}/pages/{attempt}/{random}`     | Checkpoints                            | Permanently, once in the page catalog      |
| `orgs/{org}/runs/{run}/contents/{attempt}/{random}`  | Storage binding: large binary content  | While the Run's final state references it  |
| `orgs/{org}/runs/{run}/subagents/{attempt}/{random}` | Storage binding: inline subagent state | While the Run's final state references it  |

`orgs/{org}/runs/{run}/display/…` disappears. Every key is still written once, and only committed references make bytes reachable, as the existing object contract requires. All of these objects are compressed with zstd.

The Service store writes content and subagent states the way checkpoints publish objects today: outside any database session, to a new key of the current attempt. A load verifies the digest in the reference. Each checkpoint lists the stored values its state references in `runs.checkpoint.refs`.

A value inherited from an earlier Run keeps that Run's key. It lives under the earlier Run's prefix and is kept by the earlier Run's final state.

## Cleanup

Cleanup stays owner-driven, as today ([object reclamation](../07-facts-and-delivery.md#objects)):

1. A checkpoint stages reclamation of the state and tail objects it replaced.
1. A takeover scans the Run's `state/` and `tail/` objects and deletes those of earlier attempts that the committed pointers do not name.
1. Every seal stages one scan of the Run's prefix. It keeps the final state, the final tail, every page in the catalog, and the objects listed in the final checkpoint's `refs`, and deletes everything else: replaced objects not yet reclaimed, uploads whose commit failed, and content or subagent states the final state no longer references.

The seal scan is safe because nothing writes under a Run's prefix after its seal, and a later Run or fork reaches this Run's objects only through its final state, which keeps them. As today, the scan runs after the seal has frozen the references.

## API

- `GET …/runs/{run}/items` pages by ordinal with `before`, `after`, and `limit`, returning the newest items by default. `dropped` is removed. See [reads](02-visible-history.md#reads).
- `POST …/runs/{run}/fork` accepts failed and cancelled Runs.
- The OpenAPI export changes, which notifies the SDK repositories.
- The thread stream frames are unchanged.

## Settings

- `worker.display_bytes` and the 4096-item limit no longer bound visible history.
- New settings, with initial values tuned by the load test: items per page (256), bytes per page (1 MiB), the large-content threshold (64 KiB), and the zstd compression level.

## Cutover

This is a clean breaking change, with no compatibility readers, conversions, or mixed-version operation:

- The Service state format, Display format, Run columns, head rule, and items API change together. State objects, Display objects, and subagent entries written before the cutover are not read.
- The Harness state format changes for every Host, because `a13n.subagents` entries now hold references ([Harness state and resume](../../a13n-harness/10-snapshot-and-resume.md)). With the default binding nothing leaves Harness state, but Harness UI conversations stored before the cutover are not converted either.
- Deployments back up existing data and start from a fresh store. Old and new versions never run against the same database.
