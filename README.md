<p align="center">
  <img src="frontend/packages/a13n-ui/src/brand/a13n-logo.svg" alt="a13n logo" width="96">
</p>

<h1 align="center">Agent Foundation (a13n)</h1>

**Build and run cloud agents on your own infrastructure.**

a13n brings an agent harness and managed runtime together in one open-source platform.

Choose your model, sandbox, search, and memory providers. Bring your own keys.

[Documentation](https://a13n.dev/docs/) · [Quickstart](#quickstart) · [Slack](https://join.slack.com/t/a13n/shared_invite/zt-4embv13zr-JfXjmJQop4Y9y6K3NH0kDQ)

[![Console showing six managed agents, their model providers, and status](.github/assets/console-managed-agents.jpg)](.github/assets/console-managed-agents.jpg)

*Manage agents in Console and [monitor usage across your workspace](.github/assets/console-usage.jpg). Shown with fictional demo data.*

## Quickstart

With Docker and Docker Compose installed:

```bash
mkdir a13n-service && cd a13n-service
curl -fL https://raw.githubusercontent.com/converge-ai-labs/agent-foundation/main/deploy/docker/compose/a13n-service.yaml -o a13n-service.yaml
docker compose -f a13n-service.yaml up -d --wait --pull always
```

Open **<http://127.0.0.1:8080>**, create your administrator account, connect a model provider, and create an agent. Use a provider API key or [sign in with ChatGPT](docs/a13n-service/models.md#chatgpt-subscription-provider) with an eligible plan.

Then [connect your application](docs/a13n-service/connect-application.md) with the HTTP API or a [Python, TypeScript, Go, or Rust SDK](docs/a13n-service/sdks.md).

The local stack includes Service, Console, PostgreSQL, and Redis, and uses your host Docker Engine for agent environments. See the [deployment guide](deploy/docker/compose/README.md) for a shared deployment.

## Why a13n?

- **Managed agents for your applications.** Service handles execution, persistent sessions, permissions, and recovery. Call agents through the API; use Console to configure, manage, and observe them.
- **Harness and service, built together.** Tools, Skills, memory, and subagents share one execution foundation, available as an embeddable library and a managed runtime. See our [Terminal-Bench evaluation](https://github.com/converge-ai-labs/agent-foundation/discussions/1015) and [FrontierHarness evaluation](https://github.com/converge-ai-labs/agent-foundation/discussions/1064) for results and methodology.
- **Your infrastructure, your providers.** Self-host the full platform under Apache 2.0. Choose providers for [models](docs/a13n-service/models.md), [sandboxes](docs/a13n-service/environments.md), [search](docs/a13n-service/tools.md#web-search-and-scrape), and [memory](docs/a13n-service/memory.md), using your own credentials.

## Explore

- [Service](docs/a13n-service/index.md): build agent applications on the managed runtime.
- [Harness](docs/a13n-harness/getting-started.md): embed agent execution in your own process.
- [Harness UI](docs/a13n-harness-ui/index.md): a terminal and browser workbench over the same Harness.
- [Examples](examples/README.md) · [Contributing](CONTRIBUTING.md) · [Issues](https://github.com/converge-ai-labs/agent-foundation/issues) · [Slack](https://join.slack.com/t/a13n/shared_invite/zt-4embv13zr-JfXjmJQop4Y9y6K3NH0kDQ)

These guides track `main`. For an installed release, use its release notes and package metadata.

[Apache 2.0](LICENSE) · [Security](SECURITY.md)
