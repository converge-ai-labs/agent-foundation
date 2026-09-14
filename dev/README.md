# Development support

| Directory                                      | Responsibility                                                                |
| ---------------------------------------------- | ----------------------------------------------------------------------------- |
| [service](service/README.md)                   | Local Service configuration, infrastructure and repeatable empty/seeded state |
| [harness](harness/README.md)                   | Harness SDK development environment and observation scenarios                 |
| [harness-ui](harness-ui/README.md)             | CLI/App development environment and scripted smoke workflow                   |
| [live_tests](live_tests/README.md)             | Opt-in HTTP integration and recovery journeys with their own fixtures         |
| observability                                  | Local trace-backend infrastructure, including Langfuse                        |
| [observation-demo](observation-demo/README.md) | Runnable observation demonstration                                            |

Keep configuration, source fixtures and tools together under their owning purpose. Generated local Service state belongs in the ignored root `var/` directory. The root Makefile is the stable developer command interface.
