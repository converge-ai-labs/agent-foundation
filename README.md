<p align="center">
  <img src="frontend/packages/a13n-ui/src/brand/a13n-logo.svg" alt="a13n logo" width="96">
</p>

<h1 align="center">Agent Foundation (a13n)</h1>

[![CI](https://github.com/converge-ai-labs/agent-foundation/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/converge-ai-labs/agent-foundation/actions/workflows/ci.yml) [![Documentation](https://img.shields.io/badge/docs-agent--foundation-blue)](https://a13n.dev/docs/) [![Python](https://img.shields.io/badge/python-3.13%2B-blue)](https://www.python.org/) [![License](https://img.shields.io/badge/license-Apache--2.0-green)](LICENSE) [![Slack](https://img.shields.io/badge/Slack-Join%20the%20community-4A154B)](https://join.slack.com/t/a13n/shared_invite/zt-4embv13zr-JfXjmJQop4Y9y6K3NH0kDQ)

**Build and run cloud agents on your own infrastructure.**

a13n is an open-source managed agent service with an integrated harness. Built for long-running tasks, durable execution, and [high availability](docs/a13n-service/operations.md#process-roles).

Choose your model, sandbox, search, and memory providers. Bring your own keys.

[Quickstart](#quickstart) · [Connect your app](docs/a13n-service/connect-application.md) · [SDKs](docs/a13n-service/sdks.md)

[![Console showing six managed agents, their model providers, and status](.github/assets/console-managed-agents.jpg)](.github/assets/console-managed-agents.jpg)

*Manage your agents in Console. Shown with fictional demo data.*

[Run details (scripted demo)](.github/assets/console-run-details.jpg) · [Workspace usage](.github/assets/console-usage.jpg)

## Quickstart

With Docker and Docker Compose installed:

```bash
mkdir a13n-service && cd a13n-service
curl -fL https://raw.githubusercontent.com/converge-ai-labs/agent-foundation/main/deploy/docker/compose/a13n-service.yaml -o a13n-service.yaml
docker compose -f a13n-service.yaml up -d --wait --pull always
```

Open **<http://127.0.0.1:8080>**:

1. Create your administrator account.
2. Open **Models → Add model**, connect a provider, and select a model. Use an API key or [sign in with ChatGPT](docs/a13n-service/models.md#chatgpt-subscription-provider) with an eligible plan.
3. Create an agent with that model, then use **Connect to app** for its ID, Service URL, and an API key.

[Connect your application](docs/a13n-service/connect-application.md) through the HTTP API or a [Python, TypeScript, Go, or Rust SDK](docs/a13n-service/sdks.md).

The local stack includes Service, Console, PostgreSQL, and Redis, and uses your host Docker Engine for agent environments. See the [deployment guide](deploy/docker/compose/README.md) for a shared deployment.

## Why a13n?

- **Managed agents for your applications.** Tasks continue when clients disconnect, recover from saved progress after worker failures, and can wait for human approval. Call agents through the API; use Console to configure, manage, and observe them.
- **Harness and service, built together.** Tools, Skills, memory, and subagents share one execution foundation. **58.06% on 62 Terminal-Bench 4.0 tasks**, versus 58.77% in published Codex results with the same model and reasoning effort. [Methodology](https://github.com/converge-ai-labs/agent-foundation/discussions/1015) · [FrontierHarness evaluation](https://github.com/converge-ai-labs/agent-foundation/discussions/1064).
- **Your infrastructure, your providers.** Self-host the full platform under Apache 2.0. Choose providers for [models](docs/a13n-service/models.md), [sandboxes](docs/a13n-service/environments.md), [search](docs/a13n-service/tools.md#web-search-and-scrape), and [memory](docs/a13n-service/memory.md), using your own credentials.

[![Service and Harness connect your applications and Console to models, environments, memory, web data, and MCP connections, with Harness UI as an interactive workbench](.github/assets/a13n-architecture.webp)](.github/assets/a13n-architecture.webp)

## Explore

- [Service](docs/a13n-service/index.md): build agent applications on the managed runtime.
- [Harness](docs/a13n-harness/getting-started.md): embed agent execution in your own process.
- [Harness UI](docs/a13n-harness-ui/index.md): a terminal and browser workbench over the same Harness.
- [Examples](examples/README.md) · [Contributing](CONTRIBUTING.md) · [Issues](https://github.com/converge-ai-labs/agent-foundation/issues) · [Slack](https://join.slack.com/t/a13n/shared_invite/zt-4embv13zr-JfXjmJQop4Y9y6K3NH0kDQ)

These guides track `main`. For an installed release, use its release notes and package metadata.

[Apache 2.0](LICENSE) · [Security](SECURITY.md)
