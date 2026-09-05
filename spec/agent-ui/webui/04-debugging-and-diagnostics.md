# Activity and Diagnostics

## Design Position

Detailed inspection is progressive disclosure inside the conversation shell and Settings. Ordinary conversation shows messages and concise semantic activity. Selecting an activity summary opens exact read-only detail in the optional context panel; an explicit `View activity` action replaces the conversation body with the complete activity log for the same root Thread. App, listener, schema, and configuration health belong to Settings diagnostics.

Activity is not a second execution surface. It has no composer, does not create another focused stream, and does not turn an event selection into control authority. Returning to the conversation restores the ordinary composer and any unsent draft owned by that mounted Thread feature.

## Disclosure Levels

The same `AgentUiApp` facts appear through three bounded levels:

| Level                | Primary question                                 | Presentation                                                                                     |
| -------------------- | ------------------------------------------------ | ------------------------------------------------------------------------------------------------ |
| Conversation summary | What is the Agent doing, and does it need me?    | Inline reasoning, tool, task, child, decision, and outcome summaries with exceptional state      |
| Context detail       | What exact fact explains this selected activity? | One optional right-side panel with safe summary, payload/result, schema, timing, and correlation |
| Activity log         | What happened across this complete root lineage? | Read-only ordered operation, event, child, task, state, and timing views                         |

The conversation remains primary. Successful settled intermediate work collapses to a compact summary. Running, failed, denied, interrupted, or decision-bearing work remains expanded enough to explain its state. A summary never claims that omitted detail is unavailable; it exposes a detail action only when the current projection supplies an exact correlation.

## Thread Route and Selection

`/threads/$threadId` remains the only canonical route for both conversation and activity inspection. Typed search parameters can select:

- the primary `conversation` or `activity` view;
- a closed panel, the Environment panel, or one activity-detail panel;
- one exact stable or process-local correlation supported by the selected view.

Changing the primary view or panel reuses the mounted root Thread feature and its single focused controller. It does not close and reopen a competing subscription. A stable retained correlation can survive reload. A process-local correlation that is no longer available yields an explicit unavailable selection while the enclosing Thread remains inspectable; the browser never selects a similar later event.

A link from Settings diagnostics to a Thread names the stable Thread ID and optional exact selection. A link from activity to Settings names only an owning diagnostic or source identity; it does not copy source-management behavior into the Thread route.

## Environment Context

The Environment panel explains the context in which the current or latest Run operates without becoming a filesystem browser. It can include only detached App-projected facts:

- current Thread Project and configured roots;
- selected Environment profile, mode, and authority description;
- current or latest Run working root and path layout;
- canonical Host paths whenever the selected profile projection sets `canonical_host_paths=true`;
- virtual Environment paths and advanced mount mapping otherwise;
- bounded active process, source-reference, Asset-reference, and Environment-state summaries when available.

The panel does not enumerate arbitrary Host directories, read files, infer Git state, or expose environment-variable values. A missing fact is absent or explicitly unavailable rather than synthesized from a displayed path. Editing Project roots, Environment profiles, Agents, Plugins, or other desired resources navigates to Settings and never mutates an admitted Run composition.

## Activity Log

The activity log provides mutually exclusive views around one selected diagnostic concern:

- **Timeline** orders retained transcript facts and available current-process live activity by their owning correlation;
- **Operation** presents current-App-lifetime receipt availability, Run status or terminal outcome, output or failure, usage, continuation selection, Environment-state publication, and cleanup facets;
- **Children** presents descendant execution lineage with persisted and local status separated;
- **Tasks** presents the bounded Working State projection selected from the current continuation plus clearly provisional live changes;
- **State** presents Thread metadata/configuration versions, selected continuation, focused epoch/cutover, and bounded availability facts.

Selecting one row can open the context detail panel with the safe views the App supplies: summary, arguments or payload, result, diff or managed effect, schema identity, and timing. The panel is closed by default, changes no App state, and clears when its selected correlation no longer belongs to the inspected Thread.

The Timeline is a diagnostic ledger, not a second transcript. It favors precise kinds, correlation, order, and omission facts over conversational typography. The conversation remains the only place that presents the ordinary turn loop, deferred-response controls, and composer.

## Retained and Process-local Limits

Agent UI does not add a durable raw event log for browser inspection. Activity can combine only:

- retained transcript entries from the selected continuation;
- saved compact child checkpoints;
- current-App-lifetime root-operation views supplied by App projections;
- the focused snapshot's process-local presentation;
- later detailed live events still available in the current App epoch;
- bounded safe review projections exposed through exact correlation.

A process restart removes receipt, active-operation, terminal-operation, and detailed live facts owned by that App lifetime. An expired ring cursor, dropped detail, or omitted payload is likewise shown as unavailable. Activity never reconstructs raw Harness messages, a prior live sequence, native deferred values, mutable Project files, or historical tool detail from approximate text and timestamps.

Retained and provisional rows use distinct labels. A terminal-looking live frame does not prove continuation selection, state publication, cleanup, or checkpoint persistence; the corresponding retained operation and continuation facets remain independently visible.

## Settings Diagnostic Entry

`/settings/diagnostics` presents the authenticated status projection owned by the HTTP adapter: App lifecycle and reachability, API schema and bundled asset compatibility, listener bind address, and API-key enforcement or explicit dangerous-bypass mode. It links configuration health and repair through [Configuration Diagnostics](03-configuration-and-management.md#configuration-diagnostics), which remains the sole owner of accepted-generation, candidate-source, catalog, and account-required-action behavior.

Thread and Run inspection navigates to the canonical Thread route with activity selected. Configuration repair navigates to the owning guided Settings editor or its advanced exact-source view. Settings diagnostics does not expose arbitrary logs, SQLite tables, immutable-object paths, native stack traces, API keys, Model credentials, OAuth material, or environment-variable values.

## Responsive Presentation

Wide conversation layout can add one context panel without obscuring the primary content. Medium layouts turn the panel into a drawer. Narrow layouts show conversation or activity and selected detail as separate route-preserving panels with explicit back navigation. A width change never replaces a selected correlation or changes whether a fact is retained, provisional, omitted, or unavailable.

The activity log can use denser tabular presentation than the conversation, but every row remains keyboard navigable and every selected detail has a labeled non-tabular reading order. Large payloads, diffs, and child trees remain bounded and mount only when selected.

## Failure Semantics

| Failure                           | Browser behavior                                                                                               |
| --------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| Thread missing or inaccessible    | Preserve the shell and present the exact missing identity                                                      |
| Focused stream reset              | Discard provisional rows and establish a fresh subscribe-before-query snapshot without altering retained facts |
| Process-local correlation expired | Keep the enclosing view and mark the selected detail unavailable                                               |
| Retained continuation changes     | Replace continuation-bound pages and selections rather than combining histories                                |
| Payload omitted or truncated      | Present the App omission fact; never display an invented empty value                                           |
| App unavailable or restarting     | Keep intended route and show reconnect state without replaying commands                                        |
| Configuration issue selected      | Navigate to the owning Settings editor without duplicating its source projection                               |

## Invariants

1. Conversation owns ordinary interaction and control; activity and diagnostics are read-only disclosures.
2. Conversation, activity log, and contextual detail for one root Thread share one focused controller.
3. Activity never creates another execution, storage, continuation, or event-history authority.
4. Stable and process-local correlations fail explicitly when unavailable and never fall through to a similar fact.
5. Retained and provisional activity remain distinguishable.
6. Browser inspection exposes only detached bounded safe projections, never native Harness or storage values.
7. Desired-resource health and repair remain in Settings; Thread and Run inspection remain on the canonical Thread route.
8. Responsive layout changes presentation only and preserves the selected identity.
