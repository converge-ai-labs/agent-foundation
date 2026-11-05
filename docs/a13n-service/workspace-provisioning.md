---
title: Configure workspace provisioning
description: Provision default execution resources and select sandbox images for a deployment.
---

Use this deployment recipe to create default Local or Docker execution resources for workspaces. Automatic provisioning runs only in the all role; split control and worker deployments configure resources explicitly. Set up Engine access before enabling Docker provisioning, and verify an actual environment after the resources are created.

## Workspace provisioning

Automatic Local/Docker provider and template creation runs only in the `all` role. Both are disabled in generic settings; the supplied socket-enabled Compose stack enables Docker and leaves Local off. Split control/worker deployments configure execution resources explicitly.

For Docker initialization, grant the Service process access to the intended Engine, through `environments.docker_host` or its Docker environment. A CLI installation alone gives no Engine access. Then enable:

```toml
[provisioning.docker]
enabled = true
```

The default template uses Service's reviewed sandbox image and `pull_policy = "if_missing"`. Provisioning creates resources once; initialization neither pulls an image nor creates a container. The image is pulled when an instance needs it. Later setting changes do not overwrite existing templates: edit those in Console or through the API. Existing instances keep their image.

For a locally built image in a repository checkout:

1. Run `make image-sandbox` from the repository root.
2. Save this override as `compose.local.yaml` in that root:

```yaml
services:
  service:
    environment:
      A13N_PROVISIONING__DOCKER: '{"enabled":true,"image":"a13n-sandbox:local","pull_policy":"never"}'
```

3. Start the stack with both files:

```sh
docker compose -f deploy/docker/compose/a13n-service.yaml -f compose.local.yaml up -d --wait
```

For direct TOML deployment, put the same `image` and `pull_policy` fields in `[provisioning.docker]`. Use `if_missing` for registry images. Environment overrides replace the complete JSON object; the old `A13N_DOCKER_ENVIRONMENT_IMAGE` shortcut is no longer read.

For Local execution in a development deployment only, enable it with an explicit absolute root:

```toml
[provisioning.local]
enabled = true
root = "/srv/a13n/environments"
```

Local executes directly on the worker host without isolation. Disabling it preserves directories and resources but makes Local unavailable for subsequent execution. Docker provisioning disablement leaves manually configured Docker providers usable. See [workspace provisioning](environments.md#workspace-provisioning) for retry and ownership behavior.
