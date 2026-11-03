[![a13n architecture showing Service, Harness, clients, and providers](.github/assets/a13n-architecture.webp)](.github/assets/a13n-architecture.webp)

<h1 align="center">Agent Foundation (a13n)</h1>

[![CI](https://github.com/converge-ai-labs/agent-foundation/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/converge-ai-labs/agent-foundation/actions/workflows/ci.yml) [![Documentation](https://img.shields.io/badge/docs-agent--foundation-blue)](https://a13n.dev/docs/) [![Python](https://img.shields.io/badge/python-3.13%2B-blue)](https://www.python.org/) [![License](https://img.shields.io/badge/license-Apache--2.0-green)](LICENSE) [![Slack](https://img.shields.io/badge/Slack-Join%20the%20community-4A154B)](https://join.slack.com/t/a13n/shared_invite/zt-4embv13zr-JfXjmJQop4Y9y6K3NH0kDQ)

**Build and run cloud agents on your own infrastructure.**

a13n is an open-source managed agent service with an integrated harness. It supports long-running tasks, durable execution, and [high-availability deployments](docs/a13n-service/operations.md#process-roles).

Use your own keys with your choice of [model](docs/a13n-service/models.md), [sandbox](docs/a13n-service/environments.md), [search](docs/a13n-service/tools.md#web-search-and-scrape), and [memory](docs/a13n-service/memory.md) providers.

## Quickstart

Install Docker and Docker Compose, then run:

```bash
mkdir a13n-service && cd a13n-service
curl -fL https://raw.githubusercontent.com/converge-ai-labs/agent-foundation/main/deploy/docker/compose/a13n-service.yaml -o a13n-service.yaml
docker compose -f a13n-service.yaml up -d --wait --pull always
```

Open <http://127.0.0.1:8080>:

1. Create your administrator account.
2. In **Models**, select **Add model**, connect a provider, and choose a model. Use an API key or [sign in with ChatGPT](docs/a13n-service/models.md#chatgpt-subscription-provider) with an eligible plan.
3. Create an agent with that model. Open **Connect to app** for its ID, Service URL, and an API key.

Call the agent from your application through the [HTTP API](docs/a13n-service/connect-application.md) or a [Python, TypeScript, Go, or Rust SDK](docs/a13n-service/sdks.md).

The stack runs Service, Console, PostgreSQL, and Redis. Agent environments use your host Docker Engine. For a shared deployment, follow the [deployment guide](deploy/docker/compose/README.md).

[![Console showing six managed agents, their model providers, and status](.github/assets/console-managed-agents.jpg)](.github/assets/console-managed-agents.jpg)

Console with fictional demo data. See [run details from a scripted demo](.github/assets/console-run-details.jpg) and [workspace usage](.github/assets/console-usage.jpg).

## Service and Harness

Service keeps tasks running when clients disconnect and resumes them from saved progress after worker failures. Agents can wait for human approval. Use Console to configure agents and inspect their execution.

Service and Harness UI use the same Harness for tools, Skills, memory, and subagents. You can also embed Harness in your own process.

Harness scored 58.06% on 62 Terminal-Bench 4.0 tasks. Published Codex results were 58.77% with the same model and reasoning effort. See the [methodology](https://github.com/converge-ai-labs/agent-foundation/discussions/1015) and our [FrontierHarness evaluation](https://github.com/converge-ai-labs/agent-foundation/discussions/1064).

## Documentation

- [Service](docs/a13n-service/index.md): integrate managed agents into your application.
- [Harness](docs/a13n-harness/getting-started.md): embed agent execution in your process.
- [Harness UI](docs/a13n-harness-ui/index.md): work with agents in a terminal or browser.
- [Examples](examples/README.md)

These guides track `main`. For an installed release, use its release notes and package metadata.

Ask questions on [Slack](https://join.slack.com/t/a13n/shared_invite/zt-4embv13zr-JfXjmJQop4Y9y6K3NH0kDQ) or [open an issue](https://github.com/converge-ai-labs/agent-foundation/issues) for bugs and feature requests. Read [CONTRIBUTING.md](CONTRIBUTING.md) before submitting changes.

You can self-host a13n under the [Apache 2.0 license](LICENSE). See [SECURITY.md](SECURITY.md) to report a vulnerability.
