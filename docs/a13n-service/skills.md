---
title: Skills
description: Package instructions and files as revisioned skills that agents load on demand.
---

A skill is a package of instructions and supporting files that an agent can load when a task calls for it. Skills are [revisioned](resources.md#lifecycles): each new package adds an immutable revision, and agent revisions pin the exact skill revision they use. See [Harness skills](../a13n-harness/skills.md) for how agents use them at run time.

A skill is identified by its [ID](resources.md#common-conventions) (`sk_…`), which paths such as `/api/v1/skills/{skill_id}` and agent configurations use. The model sees the skill by the `name` that the `SKILL.md` of its pinned revision declares. Two skills of a workspace may declare the same name, and a new revision may declare another, but the skills of one agent must declare distinct names.

## Package format

A skill package is a zip archive with `SKILL.md` at its root or inside its only top-level directory. `SKILL.md` is UTF-8 with YAML front matter that declares at least `name` and `description`:

```markdown
---
name: release-notes
description: Write release notes from merged pull requests.
---

# Release notes

1. List the merged pull requests since the last tag...
```

The declared `name` must be one portable directory component, at most 255 UTF-8 bytes: no separators, `.` or `..`, reserved device names, or reserved filename characters.

Limits: at most 1000 files, 8 MiB per file, 32 MiB expanded, 256 KiB for `SKILL.md`, and paths up to 1024 bytes. Archive entries must be regular files with relative paths, stored or deflated, and not encrypted.

## Add a skill

In Console, open **Skills → Import skill** and choose a ZIP file or a GitHub repository. Through the API, a skill's source is either an upload or a public GitHub directory:

```sh
# Stage the archive (see Uploads), then create the skill from it
curl -X POST "$A13N_URL/api/v1/uploads" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Idempotency-Key: release-notes-1" -F file=@release-notes.zip

curl -X POST "$A13N_URL/api/v1/skills" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Content-Type: application/json" \
  -d '{"source": {"kind": "upload", "upload_id": "upl_..."}}'
```

```json
{"source": {"kind": "github", "repository": "owner/repo", "ref": "main", "path": "skills/release-notes"}}
```

- The response's `id` (`sk_…`) identifies the skill in paths and agent configurations. `name` and `description` default to those in `SKILL.md`; pass them to override.
- GitHub imports read public repositories anonymously. `ref` defaults to the default branch and `path` to the repository root. The resolved commit is recorded; pass `commit` to require a specific one (`409 conflict`, reason `commit_mismatch`, otherwise). Each GitHub request is bounded by `control.import_timeout`.
- Upload size is bounded by `objects.upload_bytes` (1 MiB by default).
- `POST …/skills/validate` with `{"source": ...}` checks a package exactly as creation would and returns its manifest (name, description, files, sizes, digest and resolved source) without storing anything.

## Revisions

- `POST …/skills/{skill_id}/revisions` with `{source, note?, make_default?}` and the skill's `If-Match` adds a revision; it becomes the default unless `make_default` is `false`. The package's `SKILL.md` may declare another `name`. A package identical to the current default is a no-op: the call returns that revision again (`201`) without creating one, regardless of `make_default`.
- `GET …/revisions` lists revisions newest first; `POST …/revisions/{revision_id}/set-default` changes the default. A revision's `skill_id` names its skill.
- `GET …/revisions/{revision_id}/content` downloads the archive, and `GET …/revisions/{revision_id}/files/{path}` one file of it.
- `PATCH …/skills/{skill_id}` changes `name`, `description` and `labels`. Lists filter by `label`, `q` (name or description), `archived` and `source` (`upload` or `github`, of the default revision).
- `POST …/archive` stops new agent revisions from pinning the skill and refuses changes; agents that already pin it keep working. `POST …/unarchive` reverses it.

An agent revision selects skills in `skills` as `{"skill_id": "sk_…", "revision_id": ...}`; without `revision_id`, saving pins the skill's current default revision. `GET /api/v1/agents?skill_id=sk_…` lists the agents with a revision that pins the skill; `skill_revision_id` filters by one skill revision.

## Runtime synchronization

Configure all selected skills with one `skill_sync` block in the agent configuration (Console's advanced JSON). The default is shared, last-publication-wins:

```json
{
  "skills": [{"skill_id": "sk_…"}],
  "skill_sync": {"mode": "shared", "overwrite": true, "cleanup": false}
}
```

Shared mode publishes each selected package at `/workspace/.agents/skills/<name>` before the agent's first model request. Overwriting replaces the whole directory, including removal of obsolete files. Set `overwrite: false` to preserve existing names. Set `cleanup: true` to remove unselected entries from that shared directory; an empty selection then clears it. Shared mode does not use the package cache.

Each skill directory is published from a complete staged package. Linux and macOS use atomic replacement on supported filesystems, preserving the old directory if publication fails. Windows uses ordinary remove/rename for an existing directory. This keeps Windows usable while accepting a visibility gap and a missing or partially removed destination if replacement fails. Other unsupported atomic replacement operations fail without changing the old directory. Cancellation or an uncertain provider outcome can leave the replacement published or the candidate staged.

Shared paths can change while another run or child agent reads them. The last publication wins, regardless of run completion order. Separate reads can span versions, and the complete selection is not one atomic update. Cleanup can delete skills another active agent still needs. Leave the default `cleanup: false` when independent writers select different skills, or coordinate ownership of the shared directory.

When agents need conflicting versions in the same environment, choose isolated mode:

```json
{"skill_sync": {"mode": "isolated", "cache_count": 128}}
```

Its catalog paths are `/workspace/.agents/skill-cache/<package-digest>/<name>`. Different packages coexist, while identical archive bytes reuse the same directory without downloading again. `cache_count` is a nonnegative soft package-count budget for this environment; `0` disables trimming. Each agent applies its own configured budget when materializing. Active packages stay pinned until execution teardown; bounded least-recently-used trimming removes idle packages and may leave the cache above budget. Shared cleanup never touches this cache.

Isolated mode shares immutable package content, rather than allocating a copy per agent. Immutability is a Service publication guarantee; authorized file and shell operations can still modify the files. Cache publication failure leaves existing packages intact. Cancellation or uncertain mutation outcomes retain protection against eviction, and can leave staging directories or pins for later operator cleanup.

| Scenario                                  | Result                                                                                                          |
| ----------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| Shared, different names, cleanup disabled | Both names remain available.                                                                                    |
| Shared, same name and identical package   | Overwrite republishes equivalent content.                                                                       |
| Shared, same name and different packages  | The last publication wins, including for readers already running; Windows accepts the remove/rename gap.        |
| Shared, `overwrite: false`                | The existing name or concurrent winner stays, even if it differs from the selected revision.                    |
| Shared, `cleanup: true`                   | Unselected names are removed, including those used by another agent; an empty selection clears the shared root. |
| Isolated, identical package bytes         | Agents and revisions reuse the same directory.                                                                  |
| Isolated, different versions of one name  | Each catalog uses its own digest path; both versions coexist.                                                   |
| Isolated, cache over budget               | Active packages remain pinned; bounded trimming evicts idle packages in least-recently-used order.              |
| Shared and isolated in one environment    | Separate roots keep shared overwrite and cleanup away from the isolated cache.                                  |

Use `options.overrides.skill_sync` to select a policy for one run:

```json
{"options": {"overrides": {"skill_sync": {"mode": "isolated"}}}}
```

The override replaces the entire block; omitted members use the chosen mode's defaults. Children use their own agent policies. `overwrite` and `cleanup` belong only to shared mode; `cache_count` belongs only to isolated mode. Selecting skills or requesting shared cleanup requires a primary environment.

The new layouts replace the former `.a13n/skills` layout without reading or migrating it. Cache coordination requires coherent shared-filesystem operations across environment clients. A crash or uncertain provider operation can leave staging directories, pins or eviction gates. Quiesce all clients before manually reclaiming abandoned state; protection has no timed expiry.
