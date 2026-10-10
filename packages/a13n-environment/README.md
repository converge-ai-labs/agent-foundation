# a13n-environment

Independent Python Environment Provider definitions and implementations for local directories, Docker, cloud sandboxes, and Envd targets. Native providers use operating-system or vendor APIs; Envd providers use `a13n-envd-client` and EIP.

The package owns single-environment operations and their resources. Consumers own target selection, authorization, durable state, and lifecycle policy. Harness adds Run-local mounts, permissions, tools, and model context; the Environment library does not depend on Harness.

Install the `docker`, `e2b`, or `modal` extra when using that provider. Importing the package does not load optional vendor SDKs or open connections.

The [Environment specification](../../spec/a13n-environment/README.md) owns the architecture, public contract, and backend guarantees.
