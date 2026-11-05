---
title: Skill publication reference
description: Choose shared or isolated package publication and handle cleanup safely.
---

Use this reference after [importing and testing a workspace skill](skills.md#add-a-skill). Choose shared publication for conventional paths, or isolated publication when concurrent agents must keep distinct package versions. These rules apply to imported workspace skills, not read-only built-in Host skills.

## Runtime synchronization

Choose a publication mode in the agent's advanced JSON. The mode controls where skill files live in the primary environment:

| Need                                                   | Mode               |
| ------------------------------------------------------ | ------------------ |
| Use conventional shared skill paths                    | `shared` (default) |
| Run different versions with the same name concurrently | `isolated`         |

Selecting imported workspace skills or requesting shared cleanup requires a primary environment. Each child agent uses its own policy.

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
