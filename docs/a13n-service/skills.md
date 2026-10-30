---
title: Skills
description: Import a workflow as a skill, pin it to an agent, and choose how its files are published.
---

Use a skill to give an agent reusable instructions and supporting files. The agent sees a short catalog entry and reads the relevant `SKILL.md` when needed. This page takes a release-notes skill from a local file to an agent run.

You need workspace `write` permission to import a skill and a [primary environment](environments.md) for a custom agent to load it. Configure the [tools](tools.md) the workflow needs separately. See [Harness skills](../a13n-harness/skills.md) for the catalog and on-demand reading behavior.

## Built-in skills

To teach an agent Service operations, select **platform-operations** in its **Skills** section and save a revision. The agent-configuration fragment is:

```json
{"builtin_skills": ["platform-operations"]}
```

Enable the relevant [platform tools](tools.md#platform-tools) too. Selecting the skill supplies instructions, not tool permissions.

`GET /api/v1/builtin-skills` lists release-owned skills. In Console, open one to browse `SKILL.md`, references and the deployment catalog, preview source, or download its ZIP. Built-in skills update with the deployment; they have no workspace ID or independent revision history. The platform package includes `schemas/agent-config.json` and `instance/catalog.json`.

Custom agents, including Agent Composer copies, load both built-in and imported skills into their primary environment using `skill_sync`. Choose a session environment or a default environment template before running them; a missing primary environment fails before the model call. Built-in agents use their read-only Host files instead. Selected built-in and imported skills must have distinct declared names.

## Package format

Create a directory for the package and save this file as `SKILL.md`:

```markdown title="SKILL.md"
---
name: release-notes
description: Write release notes from a supplied list of merged pull requests.
---

# Release notes

1. Read the supplied pull-request titles and descriptions.
2. Group changes under Features, Fixes, and Maintenance; omit empty groups.
3. Write one concise bullet per change and include its supplied PR number.
4. Ask for missing source details instead of inventing changes or PR numbers.
```

From that directory, use Python 3 to create the ZIP:

```sh
python -m zipfile -c release-notes.zip SKILL.md
```

You can include supporting files and refer to them by relative path from `SKILL.md`. Put `SKILL.md` at the archive root or inside its only top-level directory. The document must be UTF-8 with YAML front matter containing `name` and `description`.

The declared name becomes the model-visible name and directory component. Use a portable name such as `release-notes`: at most 255 UTF-8 bytes, without separators, `.` or `..`, reserved device names or reserved filename characters.

| Package limit    | Maximum    |
| ---------------- | ---------- |
| Files            | 1000       |
| One file         | 8 MiB      |
| Expanded package | 32 MiB     |
| `SKILL.md`       | 256 KiB    |
| Relative path    | 1024 bytes |

Archive files must use relative paths and stored or deflate compression. Links and encrypted entries are rejected. The upload endpoint has a separate size limit: `objects.upload_bytes`, 1 MiB by default.

## Add a skill

1. Open **Skills → Import skill** in Console and choose `release-notes.zip`. You can also import a directory from a public GitHub repository.
2. Open the agent, select the imported skill, and save a revision. Saving pins the exact skill revision.
3. Start a conversation with a [primary environment](environments.md). Supply a few merged PR descriptions and ask the agent to use `release-notes`.
4. Inspect the run: the agent should read `SKILL.md` and produce grouped release notes from your supplied input.

For an HTTP integration, first [upload the ZIP](files-and-webhooks.md#uploads) and retain its returned `upload_id`. Send this body to `POST /api/v1/skills`, replacing `upl_...` with that ID:

```json
{"source": {"kind": "upload", "upload_id": "upl_..."}}
```

Alternatively, use this source body with your public repository, ref and directory:

```json
{"source": {"kind": "github", "repository": "owner/repo", "ref": "main", "path": "skills/release-notes"}}
```

Service reads GitHub anonymously and records the resolved commit. Omit `ref` for the default branch or `path` for the repository root. An optional `commit` requires an exact resolved commit; a mismatch returns `409 conflict` with reason `commit_mismatch`. Each GitHub request is bounded by `control.import_timeout`.

To check a source before creating the skill, send the same body to `POST /api/v1/skills/validate`. It returns the validated manifest—name, description, files, sizes, digest and resolved source—without storing the package. GitHub validation still uses the caller's upload budget.

Retain the created skill's `id` (`sk_...`) for URLs and agent configuration. Its resource name and description default to the package's front matter and can be overridden for display. The model-visible name always comes from the pinned `SKILL.md`; changing the display name does not rename the skill in the model's catalog.

## Revisions

To update the workflow:

1. Edit the package and import it through `POST /api/v1/skills/{skill_id}/revisions` with `{source, note?, make_default?}` and the skill's current `If-Match`.
2. Keep `make_default: true` (the default) to make new selections use it, or set `false` to stage a revision without changing the default.
3. Save a new agent revision selecting the desired skill revision. Existing agent revisions keep their old pins.
4. Run the same small task and inspect the new instructions and result.

An agent configuration selects `{"skill_id": "sk_...", "revision_id": "rev_..."}` in `skills`. Omit `revision_id` to pin the skill's current default when saving. A package identical to the current default returns that revision again with `201`; it creates no revision, regardless of `make_default`.

| Task                                     | API                                                                            |
| ---------------------------------------- | ------------------------------------------------------------------------------ |
| List revisions, newest first             | `GET /api/v1/skills/{skill_id}/revisions`                                      |
| Change the default                       | `POST /api/v1/skills/{skill_id}/revisions/{revision_id}/set-default`           |
| Download the ZIP                         | `GET /api/v1/skills/{skill_id}/revisions/{revision_id}/content`                |
| Read one packaged file                   | `GET /api/v1/skills/{skill_id}/revisions/{revision_id}/files/{path}`           |
| Edit display name, description or labels | `PATCH /api/v1/skills/{skill_id}`                                              |
| Find agents that pin the skill           | `GET /api/v1/agents?skill_id=sk_...`; add `skill_revision_id` for one revision |

Skill lists support `label`, `q`, `archived` and `source` filters; `source` describes the default revision. Archive a skill to stop new agent revisions from selecting it. Existing pins continue working; unarchive restores editing and selection. Different workspace skills may declare the same name, but one agent's selected skill revisions must declare distinct names.

## Runtime synchronization

Choose a publication mode in the agent's advanced JSON. The mode controls where skill files live in the primary environment:

| Need                                                   | Mode               |
| ------------------------------------------------------ | ------------------ |
| Use conventional shared skill paths                    | `shared` (default) |
| Run different versions with the same name concurrently | `isolated`         |

Selecting skills or requesting shared cleanup requires a primary environment. Each child agent uses its own policy.

### Shared directories

This configuration fragment selects a skill and makes the defaults explicit. Replace `sk_...` with the imported skill ID:

```json
{
  "skills": [{"skill_id": "sk_..."}],
  "skill_sync": {"mode": "shared", "overwrite": true, "cleanup": false}
}
```

Before the first model request, Service publishes each selected package at `/workspace/.agents/skills/<name>`. With `overwrite: true`, it replaces the whole directory and removes obsolete files. Set `overwrite: false` to preserve an existing directory, even if it differs from the selected revision.

Leave `cleanup: false` when independent agents share the environment. Enabling cleanup removes unselected entries from the shared root; an empty selection clears it, including skills another agent is using. Shared mode uses no package cache.

For the same name, the last publication wins. An active reader can therefore see another run's version, and separate reads can span versions. Publication is per skill, not atomic across the whole selection. Use isolated mode when each agent must keep reading its selected package.

### Isolated packages

Set this fragment to keep different package versions side by side:

```json
{"skill_sync": {"mode": "isolated", "cache_count": 128}}
```

The catalog points to `/workspace/.agents/skill-cache/<package-digest>/<name>`. Identical archives reuse one directory; distinct versions use distinct paths. `cache_count` is a soft package-count budget. Each agent removes idle packages according to its budget while preserving active ones, so the cache can exceed the limit. Set `0` to disable trimming.

Shared cleanup leaves this cache untouched. Service does not overwrite published isolated packages, though authorized file and shell operations can modify them. Isolated publication separates versions; it does not make files read-only.

### Override one run

To use isolated mode for a single message, include this fragment in its submission:

```json
{"options": {"overrides": {"skill_sync": {"mode": "isolated"}}}}
```

The override replaces the whole block; omitted fields use the chosen mode's defaults. `overwrite` and `cleanup` belong to shared mode; `cache_count` belongs to isolated mode. Children retain their own agent policies.

### Publication and cleanup

Service stages a complete package before publishing its directory. On supported Linux and macOS filesystems, atomic replacement preserves the old directory if publication fails. Windows replaces an existing directory with remove/rename; readers can see a gap, and a failure can leave the destination missing or partly removed. Other unsupported atomic replacements fail with the old directory intact.

After cancellation or an uncertain provider result, inspect the environment before retrying or cleaning up: the replacement may already be published, or staging and cache protection may remain. Stop all clients before manually reclaiming abandoned staging directories, pins or eviction gates. These protections have no timed expiry, and cache coordination requires coherent shared-filesystem operations across clients. The old `.a13n/skills` layout is neither read nor migrated.
