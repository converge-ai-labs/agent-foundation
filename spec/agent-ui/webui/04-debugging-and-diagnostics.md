# Debugging and Diagnostics

## Design Position

Debug is a dedicated inspection area separate from ordinary Thread interaction. The Threads area helps a user supervise activity, converse with one focused Thread, and answer decisions. Debug explains what the current App lifetime observes: root-operation facets, retained transcript structure, current-process live activity, child execution lineage, Working State tasks, selected continuation facts, listener status, and safe protocol detail.

Debug is not a second execution surface. It has no ordinary composer, does not place raw receipts or payloads beside routine conversation, and does not turn an event selection into control authority. A user returns to the Threads area to submit, steer, cancel, or answer a deferred request.

## Relationship to Threads

The Threads area and Debug provide different projections of the same `AgentUiApp` facts:

| Concern              | Threads                                              | Debug                                                                       |
| -------------------- | ---------------------------------------------------- | --------------------------------------------------------------------------- |
| Primary question     | What needs me, and what should I do next?            | What happened, and which exact fact explains it?                            |
| Default detail       | Human-readable message and semantic activity summary | Correlated operation, event, child, task, state, payload, and timing detail |
| Live stream          | One focused root lineage while a Thread is open      | One focused root lineage while that Thread is being inspected               |
| Root control         | Exact advertised actions                             | Read-only availability and correlation display                              |
| Deferred requests    | Complete decision experience                         | Safe request and continuation inspection only                               |
| Historical authority | Selected continuation and saved child checkpoints    | The same retained facts; no independent event archive                       |
| Process-local detail | Compact current activity                             | Bounded detailed activity while the App still retains it                    |

Only one focused route owns detailed live reduction. Moving from a Thread in Focus to its Debug route closes the Focus watch and establishes a fresh Debug watch; it never creates two detailed subscriptions for the same browser route tree. The App-owned Run continues through this presentation transition.

The Threads area links to Debug by stable root Thread identity and, when available, one exact operation, execution, tool-call, transcript-position, or current-process live correlation. Debug links back to the canonical Thread route without encoding Project identity; an optional validated Project origin can remain separate presentation context.

## Routes

| Route                      | Meaning                                                                                     |
| -------------------------- | ------------------------------------------------------------------------------------------- |
| `/debug`                   | Authenticated App, API-schema, listener, access-mode status, and entry to Thread inspection |
| `/debug/threads/$threadId` | Debug one root Thread and its complete descendant lineage                                   |

The Thread debug route uses typed search parameters for the selected debug view and optional detail correlation. A stable retained correlation can survive reload. A process-local correlation that is no longer available yields an explicit unavailable selection while the enclosing Thread remains inspectable; the browser does not select a similar later event.

Configuration diagnostics, repair, source mutation, catalog management, and account operations remain Configure responsibilities. Debug can link to `/configure/diagnostics` but does not repeat its accepted-generation, candidate-source, catalog, or account projections.

## Thread Debug Views

One root Thread Debug route provides mutually exclusive primary views around one selected diagnostic concern:

- **Timeline** orders retained transcript facts and available current-process live activity by their owning correlation;
- **Operation** presents current-App-lifetime receipt availability, Run status or terminal outcome, output or failure, usage, continuation selection, Environment-state publication, and cleanup facets;
- **Children** presents descendant execution lineage with persisted and local status separated;
- **Tasks** presents the bounded Working State projection selected from the current continuation plus clearly provisional live changes;
- **State** presents Thread metadata/configuration versions, selected continuation, focused epoch/cutover, and bounded availability facts.

Selecting one row can open a contextual detail pane with the safe views the App supplies: summary, arguments or payload, result, diff or managed effect, schema identity, and timing. The detail pane is closed by default, changes no App state, and clears when its selected correlation no longer belongs to the inspected Thread.

The Timeline is a diagnostic ledger, not a second transcript. It favors precise kinds, correlation, order, and omission facts over conversational typography. The Threads area remains the only place that presents the ordinary turn loop and composer.

## Retained and Process-local Limits

Agent UI does not add a durable raw event log for Debug. The area can combine only:

- retained transcript entries from the selected continuation;
- saved compact child checkpoints;
- current-App-lifetime root-operation views supplied by App projections;
- the focused snapshot's process-local presentation;
- later detailed live events still available in the current App epoch;
- bounded safe review projections exposed through exact correlation.

A process restart removes receipt, active-operation, terminal-operation, and detailed live facts owned by that App lifetime. An expired ring cursor, dropped detail, or omitted payload is likewise shown as unavailable. Debug never reconstructs raw Harness messages, a prior live sequence, native deferred values, mutable Project files, or historical tool detail from approximate text and timestamps.

Retained and provisional rows use distinct labels. A terminal-looking live frame does not prove continuation selection, state publication, cleanup, or checkpoint persistence; the corresponding retained operation and continuation facets remain independently visible.

## App Status

The Debug landing view presents the authenticated status projection owned by the HTTP adapter:

- App lifecycle and reachability;
- API schema and bundled asset compatibility;
- listener bind address;
- API-key enforcement or explicit dangerous-bypass access mode.

It can also list bounded root-Thread summaries as entry points to inspection, but a summary is not a recent event or failure archive. Debug does not expose local-store health, object counts, historical process failures, or presentation-reset history.

The status view never exposes API keys, environment-variable values, OAuth material, native stack traces, database tables, immutable-object paths, or arbitrary logs. Refresh reissues the authenticated status query. Configuration health, repair, catalog state, and account actions navigate to Configure.

## Responsive Presentation

Wide Debug can show navigation, one primary diagnostic view, and one selected detail pane. Medium layouts turn detail into a drawer. Narrow layouts show navigation, view, and selected detail as separate route-preserving panels. A width change never replaces a selected correlation or changes whether a fact is retained, provisional, omitted, or unavailable.

Debug can use denser tabular presentation than the Threads area, but every row remains keyboard navigable and every selected detail has a labeled non-tabular reading order. Large payloads, diffs, and child trees remain bounded and mount only when selected.

## Failure Semantics

| Failure                           | Debug behavior                                                                                           |
| --------------------------------- | -------------------------------------------------------------------------------------------------------- |
| Thread missing or inaccessible    | Preserve the Debug shell and present the exact missing identity                                          |
| Focused stream reset              | Discard provisional rows and establish a fresh high-water-bound snapshot without altering retained facts |
| Process-local correlation expired | Keep the enclosing view and mark the selected detail unavailable                                         |
| Retained continuation changes     | Replace continuation-bound pages and selections rather than combining histories                          |
| Payload omitted or truncated      | Present the App omission fact; never display an invented empty value                                     |
| App unavailable or restarting     | Keep intended route and show reconnect state without replaying commands                                  |
| Configuration issue selected      | Navigate to Configure diagnostics without duplicating its source projection                              |

## Invariants

1. The Threads area owns ordinary interaction and control; Debug owns detailed read-only inspection.
2. Debug never creates another execution, storage, continuation, or event-history authority.
3. One browser route tree owns at most one focused detailed subscription.
4. Stable and process-local correlations fail explicitly when unavailable and never fall through to a similar fact.
5. Retained and provisional diagnostic rows remain distinguishable.
6. Debug exposes only detached bounded safe projections, never native Harness or storage values.
7. Configuration health, changes, catalog state, and account actions remain in Configure.
8. App status contains only facts owned by the authenticated adapter projection; Debug does not invent store or history queries.
9. Responsive layout changes presentation only and preserves the selected diagnostic identity.
