# Environment Provider Specifications

## Overview

This directory defines `agent-environment-provider`, distributed as `converge-agent-environment-provider`. It is the shared Python contract and built-in implementation package for Environment provider definitions, resource management, provider resource state, and fresh runtime attachments.

Hosts and `converge-agent-harness` consume the same provider keys and configuration schemas. The package performs no durable storage and owns no Harness run, Agent loop, model-facing tool, or provider-neutral Environment operation. A Host chooses whether and how to persist desired provider specifications and resource state; the Harness converts fresh runtime attachments into single-use Environment bindings.

## Document Catalog

| Document                                                                               | Owning contract                                                                                                                    |
| -------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| [00-overview.md](00-overview.md)                                                       | Package position, architecture, boundaries, dependency direction, and stable principles                                            |
| [01-provider-specs-and-catalog.md](01-provider-specs-and-catalog.md)                   | Serializable provider specifications, typed configuration, built-in and third-party factories, validation, and compatibility       |
| [02-resource-management-and-attachments.md](02-resource-management-and-attachments.md) | Host-owned management lifecycle, resource state, reusable resources, fresh attachments, EIP session sources, failure, and recovery |
| [03-built-in-providers.md](03-built-in-providers.md)                                   | Direct Local, Docker, and E2B configuration and lifecycle behavior, SDK boundaries, and security                                   |

## Reading Paths

### Declare or persist an Environment

Read `00`, `01`, and `02`. A Host persists a provider specification and provider-owned resource-state envelope under its own authorization, encryption, fencing, and retention policy.

### Integrate the Harness

Read `00` and `02`, then [Harness Environment Integration](../agent-harness/08-environment-integration.md). The provider package returns a fresh attachment; the Harness owns attachment-to-binding adaptation and provider-neutral operations.

### Implement or review a built-in provider

Read all four documents, then the [EIP carrier contract](../agent-envd/03-transports-and-sessions.md) for Docker or E2B.

### Add a third-party provider

Read `01` and `02`. A third-party factory uses the same provider-specification, manager, resource-state, and attachment contracts and registers one namespaced key through the documented entry-point group.

## Authority Rules

- A Host owns user authorization, desired provider specifications, durable resource identity, operation fencing, resource-state persistence, lease policy, and destroy/recreate decisions.
- The provider package owns provider-specific schema validation and resource-management behavior but no durable record.
- A bound provider resource owns live provider clients, maintenance, and fresh attachment issuance for one Host scope.
- A runtime attachment carries fresh process-local access material; it is not durable state and is transferred at most once.
- The Harness owns provider-neutral Environment bindings, topology, operations, state restoration, and run-local cleanup.
- Docker and E2B use EIP for all Environment operations. Their vendor SDKs own only provider-resource lifecycle.
- Direct Local and EIP are the only Environment operation backends.

## Specification Conventions

- Python-like schemas are conceptual typed contracts unless explicitly identified as serialized documents.
- A provider specification is serializable desired configuration; a resource-state envelope is sensitive Host state; a runtime attachment is a live process-local value.
- Provider keys select installed trusted code but grant no user or run authority.
- Resource identity, Environment identity, Harness binding identity, daemon generation, and EIP session identity remain distinct.
- Provider management outcome, Harness result, and Host durable completion are independent facts.
