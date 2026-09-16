# Service SDKs

Service SDKs call a running Service. They do not execute a Harness Agent in your process and are not interchangeable with `a13n-harness`.

## Choose a repository

Each SDK repository owns its installation instructions, examples, API coverage, compatibility notes, and releases. The repositories are private; access requires permission from the organization. A repository link does not imply that a package has been published.

| Language   | Package / module                          | Repository                                                                     |
| ---------- | ----------------------------------------- | ------------------------------------------------------------------------------ |
| Python     | `a13n`                                    | [a13n-sdk-python](https://github.com/converge-ai-labs/a13n-sdk-python)         |
| Go         | `github.com/converge-ai-labs/a13n-sdk-go` | [a13n-sdk-go](https://github.com/converge-ai-labs/a13n-sdk-go)                 |
| Rust       | `a13n`                                    | [a13n-sdk-rust](https://github.com/converge-ai-labs/a13n-sdk-rust)             |
| TypeScript | `@converge.ai/a13n`                       | [a13n-sdk-typescript](https://github.com/converge-ai-labs/a13n-sdk-typescript) |

The Rust repository also owns the remote `a13n-service-cli`. It is separate from the **`a13n-service`** process/operator executable described in [Configure Service](configuration.md).

## Service contract and compatibility

SDK versions are independent of Service versions. Consult the selected SDK's README and pinned Service contract before choosing it for a deployment; do not assume equal API coverage across languages.

This documentation site owns the Service integration guides and protocol references:

- [Agents, Threads, and Runs](agents-and-runs.md): the execution workflow.
- [HTTP contracts](http-contracts.md): authentication, preconditions, idempotency, pagination, and errors.
- [Native HTTP API reference](api-reference.md) and [OpenAPI JSON](../assets/reference/service-openapi.json): the Service HTTP contract.
- [Streams, events, and gateways](streams-and-events.md): streaming and other transport boundaries.

Language-specific API reference sites belong to the SDK repositories, not this site. Separate sites are not available yet; use the repository documentation linked above.
