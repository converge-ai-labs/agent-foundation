# a13n Service

Service is Agent Foundation's managed-agent runtime, built on [Harness](../a13n-harness/README.md). It adds organizations and workspaces, configured resources, versioned agents, permissions, durable runs, and worker recovery. Console, the HTTP API, and independent SDKs expose the same managed resources. The server executable is `a13n-service`.

Use [Harness UI](../a13n-harness-ui/README.md) for an interactive playground and trusted small-team collaboration. Choose Service when applications or users need managed access and durable execution.

Start with the [deployment quickstart](../../docs/a13n-service/get-started.md) or [SDKs and CLI](../../docs/a13n-service/sdks.md). For operator commands:

```sh
a13n-service --config service.toml migrate
a13n-service --config service.toml bootstrap --email admin@example.com
a13n-service --config service.toml run --role all   # or: control, worker
a13n-service --config service.toml user disable --email someone@example.com
```

Configuration is a TOML file (`--config` or `A13N_SETTINGS_FILE`) with `A13N_<SECTION>__<FIELD>` environment overrides; see the [configuration guide](../../docs/a13n-service/configuration.md). The package layout, import rules and behavior are specified in the [Service contract](../../spec/a13n-service/README.md); local development uses [`make dev`](../../dev/service/README.md).
