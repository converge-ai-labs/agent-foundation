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

## Runtime files and cache

The primary environment makes each selected skill available at:

```text
/workspace/.agents/skills/<package-digest>/<name>/SKILL.md
```

The digest identifies the archive bytes. Identical packages reuse the same files across revisions and runs; different versions of the same name coexist. Each agent, including an inline child, sees only the versions it pins. The package is published as a complete directory, so concurrent runs do not observe a partially installed skill.

The directory immediately containing `SKILL.md` matches its declared `name`. Other tools can read that explicit path during execution; automatic discovery and duplicate-name handling depend on the tool. Service does not install symlinks or select versions for other agent CLIs. Names must be portable filename components: no separators, traversal, Windows-reserved names or characters, trailing dot or space, or more than 255 UTF-8 bytes.

Treat package files as immutable dependencies. Write generated files and local modifications outside the cache. This is a usage contract, not a filesystem permission boundary. Service uses only this Skill layout, with no legacy lookup or migration. Attachments continue using `.a13n`.

### Capacity and reclamation

`environments.skill_cache_count` (environment override `A13N_ENVIRONMENTS__SKILL_CACHE_COUNT`) defaults to **128 packages** per physical cache root. Each distinct archive digest counts once, regardless of how many agents use it. Acquisition refreshes recency and lists cache entries. Within capacity, it reads no per-package cache metadata; above capacity, it reads timestamps and reclaims up to 32 unused packages in LRU-like order. An evicted version is downloaded again when next needed.

The limit is soft: active packages remain available, and a new skill can load even when no package can safely be reclaimed. A later acquisition retries reclamation. Package count does not limit disk bytes; actual disk exhaustion can still fail a run. Set the count to `0` on all workers sharing a root to disable automatic eviction.

Reader protection lasts through the primary execution, including inline children. Use a coherently shared filesystem and the same access to its cache metadata on all workers. Disable eviction for other storage semantics or for independent consumers that need these paths after execution ends; surviving background processes are not protected.

### Interrupted cleanup

Hidden `.cache` metadata coordinates readers and collectors; `.staging` holds unpublished candidates. A crash can leave reader pins that keep a package from being reclaimed. An uncertain deletion keeps its package gate, so that digest can fail acquisition rather than risk being deleted after reuse; unrelated skills continue loading.

Before clearing stale pins, gates, incomplete packages or staging directories, stop all users of the cache root and establish that outstanding filesystem operations have ended. Do not clear coordination files by age alone. Once the root is quiescent, removing its cache allows selected revisions to be restored on their next run.
