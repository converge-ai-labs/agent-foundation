# Storage and APIs

> Discussion proposal, not the current specification. See [status and scope](README.md).

These are logical schemas, not generated DDL. Every table follows the existing tenant integrity rules, and migrations are generated with the owning Make target against a disposable database.

## PostgreSQL

```text
run_messages              # staged state: messages that differ from the start object
  run_id, position, body
  PRIMARY KEY (run_id, position)

run_namespaces            # staged state: namespaces that differ from the start object
  run_id, name, version NULL, body NULL     # NULL body removes the namespace
  PRIMARY KEY (run_id, name)

run_items                 # staged visible items
  run_id, ordinal, item_id, kind, state, content,
  first_stream_id, last_stream_id, started_at, ended_at
  PRIMARY KEY (run_id, ordinal)
  UNIQUE (run_id, item_id)

run_item_pages            # permanent history page catalog
  run_id, first_ordinal, last_ordinal, item_count, key, digest, size
  PRIMARY KEY (run_id, first_ordinal)
```

Changes to `runs`:

- `staged_state` (new): the staged header `{format, seq, attempt, message_count, resume_input_consumed, environment_states}`, null when nothing is staged.
- `display` becomes `stream_position` `{attempt, sequence, resume_after}`. The `outcome_state` check drops its Display condition, and the progress-column list of the Run trigger changes accordingly.
- `checkpoint` keeps its meaning: the Run's latest complete state object.
- A Run sealed while state is still staged may move `checkpoint` and clear `staged_state` once, through the background job. The Run trigger allows exactly this post-seal update besides labels.

Content parts larger than a threshold (initially 256 KiB), such as images, files, and large tool results, are written once as objects. Staged rows, state objects, and history pages hold references to them.

## Object store

| Key                                                | Writer                 | Lifetime                                                   |
| -------------------------------------------------- | ---------------------- | ---------------------------------------------------------- |
| `orgs/{org}/runs/{run}/state/{attempt}/{random}`   | Merges                 | Replaced objects of the same Run are reclaimed; final kept |
| `orgs/{org}/runs/{run}/pages/{random}`             | Background job         | Permanent                                                  |
| `orgs/{org}/sessions/{session}/subagents/{random}` | Subagent state binding | Permanent, never deleted individually                      |
| `orgs/{org}/sessions/{session}/contents/{random}`  | Large content parts    | Permanent, never deleted individually                      |

`orgs/{org}/runs/{run}/display/…` disappears. `checkpoint_cleanup` scans cover only the `state/` subprefix of a Run, so pages are never treated as orphans.

## Background job

One new outbox kind, `run_archive`, moves staged data to object storage outside execution:

- **History pages.** A boundary transaction stages a job when a Run has accumulated a page of finished items; every seal stages one to flush the remaining items. Jobs are keyed by Run and first ordinal.
- **Staged state of sealed Runs.** When a Run sealed without a final state object (a sweep seal, or a worker without lease time to write one), the job merges its start object and staged rows into a state object and applies the post-seal update above.

The job uses the outbox's existing leases, retries, deferral, and dead-delivery handling. Its backlog appears in the existing `a13n.backlog.size` and `a13n.backlog.oldest_age` metrics.

## API

- `GET …/runs/{run}/items` pages by ordinal with `before`, `after`, and `limit`, returning the newest page by default. `dropped` is removed. See [reads](02-visible-history.md#reads).
- The OpenAPI export changes, which notifies the SDK repositories. The change is coordinated with #815 so that it happens once.
- The thread stream frames are unchanged.

## Settings

- `worker.display_bytes` and the 4096-item limit no longer bound the whole visible history.
- New settings, with initial values tuned by the load test: items per page (256), bytes per page (1 MiB), content object threshold (256 KiB), and staged-state cap per Run (32 MiB).

## Migration and compatibility

- Existing complete state objects remain valid start objects. Their `a13n.usage` is ignored, as it is today.
- Existing Threads that used inline subagents hold version-1 `a13n.subagents` entries with complete child states. Whether the capability keeps reading that version is an [open question](04-validation-and-rollout.md#open-questions).
- Existing Display objects are not converted automatically. Their visible history is unavailable through the new API unless a one-time script converts them to history pages.
- During rolling deploys, a Run with staged state is claimable only by workers that read its staged format, extending today's `claimable()` rule.
