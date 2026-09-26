# Development support

| Directory                                      | Responsibility                                                            |
| ---------------------------------------------- | ------------------------------------------------------------------------- |
| [service](service/README.md)                   | Per-checkout local Service, Console and stores with empty or seeded state |
| [harness](harness/README.md)                   | Harness SDK development environment and observation scenarios             |
| [harness-ui](harness-ui/README.md)             | CLI/App development environment and scripted smoke workflow               |
| fixtures                                       | Shared scripted model, MCP, OAuth, Composio and mem0 development fixtures |
| observability                                  | Local trace-backend infrastructure, including Langfuse                    |
| [observation-demo](observation-demo/README.md) | Runnable observation demonstration                                        |

Keep configuration, source fixtures and tools together under their owning purpose. Generated local Service state belongs in the ignored root `var/` directory. The root Makefile is the stable developer command interface.

Automated Service end-to-end tests live in [e2e/service](../e2e/service/README.md). The [manual Console review](service/console-review.md) uses a separate disposable instance.
