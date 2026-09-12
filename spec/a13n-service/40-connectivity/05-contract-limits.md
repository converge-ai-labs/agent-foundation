# Connectivity Contract Limits

## Design Position

This document defines behavior where Connectivity has no portable cross-provider semantics. A contract limit is not an implicit default, implementation choice, or permission to infer authority.

## Non-Messaging Event Thread Selection

Inbound routing can return a new Agent Thread destination or one exact existing AgentThreadBinding. The common contract does not expose named `new`, `fixed`, `correlated`, or similar public modes.

Each exact Account target can use only its validated configuration and adapter-declared stable external references. It cannot introduce fuzzy correlation, arbitrary raw-data paths, or model-selected Thread identity. A shared public mode enumeration requires a separate accepted contract.

## Provider Messaging Details

The common messaging contract does not define Slack, Lark, Discord, or Teams wire payloads, identifier extraction, event subscription configuration, acknowledgement deadlines, retry headers, cursor formats, rate limits, or native thread creation APIs.

Each provider requires an owning adapter contract before its configuration and runtime behavior become part of the platform. That contract must map native channel, group, direct-message, topic, thread, reply-chain, mention, and message semantics into the common Account, AccountTarget, InboundEvent, messaging interaction modes, and AgentThreadBinding concepts without changing their meaning.

The common Ingress contract defines ordered per-Agent-Thread input batching and its two embedded policy controls. It does not define one universal numeric default: each provider supplies bounded defaults, and deployments constrain the accepted interval, batch count, byte, and pending-capacity ranges.

## Source Tool Compatibility

An accepted Run retains source identity, tool scope, and loading policy under [Agent-Facing External Tools](04-agent-facing-tools.md#discovery-and-recovery). Runtime discovery supplies current definitions; Service promises neither schema equality across Attempts nor semantic equivalence across Connector Providers or unrelated Remote MCP servers.

No contract promises that changing ConnectorProvider or Connection preserves tool names, schemas, effects, receipts, or results. Service guarantees only collision-free names and references across the composed Run tool surface; it does not claim that similarly named source tools are interchangeable.

## Management and User Interface

The resource model distinguishes Application Accounts, exact AccountTargets, Connector Providers, Connections. Reusable or official provider App definitions are deployment or product configuration rather than another Service resource. The contract does not prescribe whether the product displays these concepts on one page, several Console pages, or a separate Bot-facing interface.

An official a13n App can hide shared provider-application administration from a customer while preserving the customer's concrete Account identity. The exact setup, observability, and user-facing navigation are product contracts, not reception configuration semantics.

## Invariants

1. A contract limit never creates authority, compatibility, or retry claims.
2. Provider adapters and Connector Provider adapters cannot fill a missing common contract with incompatible local semantics while presenting it as portable.
