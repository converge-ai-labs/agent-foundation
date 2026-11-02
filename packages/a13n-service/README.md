# a13n Service

Service is Agent Foundation's managed-agent runtime, built on [Harness](../a13n-harness/README.md). It adds organizations and workspaces, configured resources, versioned agents, permissions, durable runs, and worker recovery. Console, the HTTP API, and independent SDKs expose the same managed resources. The server executable is `a13n-service`.

Use [Harness UI](../a13n-harness-ui/README.md) for an interactive playground and trusted small-team collaboration. Choose Service when applications or users need managed access and durable execution.

Start with the Docker [deployment quickstart](../../docs/a13n-service/get-started.md), which includes PostgreSQL, Redis and object storage, or [SDKs and CLI](../../docs/a13n-service/sdks.md) to connect an application. The following operator commands run inside the Service container or a Python environment with `a13n-service` installed and [required infrastructure](../../docs/a13n-service/configuration.md#required-infrastructure) configured:

```sh
a13n-service --config service.toml migrate
a13n-service --config service.toml bootstrap --email admin@example.com
a13n-service --config service.toml run --role all   # or: control, worker
a13n-service --config service.toml user disable --email someone@example.com
```

Configuration is a TOML file (`--config` or `A13N_SETTINGS_FILE`) with `A13N_<SECTION>__<FIELD>` environment overrides; see the [configuration guide](../../docs/a13n-service/configuration.md). The package layout, import rules and behavior are specified in the [Service contract](../../spec/a13n-service/README.md); local development uses [`make dev`](../../dev/service/README.md).

### Release Skill assets

`make a13n-service-assets` prepares the Console and release Skill schemas with the selected Service dependencies installed. The generated `builtin_skills/platform-operations/schemas/platform-tools.json` is ignored in Git and included in both wheel and sdist. Rebuilding a wheel from the prepared sdist preserves this asset without importing Service or adding runtime dependencies to the isolated build environment. Release builds reject missing prepared assets. Ordinary contract drift checks validate committed contracts; `scripts/export-a13n-service-openapi.py --skills-only --check` additionally verifies the prepared Skill schemas. Local development uses the enabled tools' discovered schemas and does not require this optional offline file.
