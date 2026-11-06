---
title: Why agent apps need more than a harness
description: The work involved in running agents reliably, managing their resources, and understanding what they do.
date: '2026-11-06'
authors: [a13n team]
---

If you're building an agent app, you might be adding an agent to an existing product or giving an internal team a way to delegate work. An existing harness is a sensible starting point. It handles the model-and-tool loop, so you can spend your time on the instructions, tools and skills the agent needs.

A harness may also support checkpoints, human approvals, MCP connections and tracing. Depending on what you're building, much of the execution code may already exist.

The application has responsibilities around that execution, though. When it accepts a task, it has to keep track of the work. When an agent uses a connection, it has to act with the right permissions. When a user reports a bad result, the team needs to find out what happened. These requirements involve the harness, but they also reach into storage, deployment, identity and the frontend. Connecting those pieces and keeping them working is the part of building an agent app that a harness alone may leave to you.

## Accepted work needs a lifecycle

Some agent features fit comfortably inside an ordinary request: the caller waits, receives a result, and moves on. Delegating longer work changes that arrangement. The app may accept a task that takes several minutes, requires approval halfway through, or produces a result the user will collect later. The task now has a lifetime of its own.

That creates a few concrete obligations for the application:

- **Acceptance:** once the app confirms a task, the input is saved and the task can be picked up even if the accepting process exits.
- **Progress:** the app can distinguish work that's queued, running, waiting for input or finished.
- **Interaction:** an approval, cancellation or follow-up reaches the execution it was intended for.
- **Results:** completed work and its outputs remain available after execution ends.

A harness may expose events and callbacks for all of these. The backend has to turn them into stored state and interfaces the rest of the app can rely on. A callback asking for approval, for instance, still needs to become a pending action that an authorized person can answer later.

An execution, usually called a **run**, needs a stable identity throughout that lifecycle. A conversation can contain several runs, and a run can include several pending actions. Those distinctions matter when inputs arrive asynchronously. Submissions also need deduplication: if a response is lost, retrying with the same idempotency key should return the existing run instead of starting the task again.

The frontend follows from this contract. It can subscribe to live events and load saved history, while the backend keeps executing independently. The UI no longer has to infer the state of the work from whether a connection is still open.

## Reliable execution extends beyond checkpoints

Once the app has accepted work, a routine deployment should not silently lose it. This applies even with one worker. Processes restart, hosts fail, and tools take longer than expected. The backend needs a record of unfinished work and a way to decide what can safely continue.

### Resuming work on another worker

A checkpoint preserves progress through the agent loop. Recovery also needs somewhere durable to store it, a process that finds interrupted runs, and access to the resources those runs were using.

In a distributed deployment, recovery has another requirement: only the current owner of a run should be allowed to commit progress. A worker that appears dead may simply be disconnected. If a replacement takes over and the old worker later returns, both can try to advance the same run. The storage and coordination layer has to reject updates from the old owner.

Separating a run from its execution attempts makes this easier to represent. The application follows one run, while the backend records the workers that attempted it and where each stopped.

![One run remains identifiable while execution moves from an interrupted worker to a replacement](execution.svg "Figure 1. A replacement worker continues the same run from saved progress. Previous workers must lose the ability to commit updates after ownership changes.")

### Recovering an external side effect

**A checkpoint cannot always establish whether a tool call took effect.** The external system may have accepted a write just before the worker lost the response. Repeating the call could repeat the action.

Recovery depends on the tool's contract. An idempotency key may make a retry safe. An external operation ID may allow the backend to look up the result. Some operations have neither, and the run has to stop for review. Retrying the entire agent loop does not resolve that uncertainty.

This responsibility remains with the tool integration even when a managed service handles execution. A service can preserve the pending operation and apply a retry policy; it cannot infer an external system's state from a missing response.

### Keeping execution resources available

Conversation state is only part of what a run uses. Files can live in a sandbox, and browser state can belong to a separate provider. Recovery fails if the checkpoint survives but the working directory or browser session it refers to is gone. Resource retention, restoration and cleanup have to agree with the run's lifecycle.

Those resources also constrain throughput. A worker needs more than an available CPU: it may need a sandbox and capacity under a model provider's quota. As users share the deployment, scheduling has to account for those limits and prevent a few expensive tasks from occupying all available capacity. Scaling the HTTP layer does not solve that problem.

## Managing agents and their dependencies

The agent an application calls will change after launch. Instructions get revised, skills are updated, and connections are added or retired. An app needs a way to keep calling the agent while those changes are tested and released.

### Configuration needs a history

Keeping agent definitions in code can work well. What matters is being able to tell which configuration a run used, and to control when a new one takes effect. The selected instructions, skill versions and relevant model settings belong with the execution record. Today's configuration may be different from the one that produced yesterday's result.

Shared resources make updates more consequential. A skill used by several agents is a dependency of each of them. Updating it requires knowing which agents will pick up the change. An MCP connection has its own maintenance work: tool definitions change, credentials expire, and servers become unavailable. Loading the tools into a harness addresses execution; maintaining the connection and its consumers is an ongoing job.

A management interface gives the team a place to inspect those dependencies, test an agent and publish a change. It needs to work against the same configuration the API uses, so testing in the interface tells the team something useful about what the application will run.

### Permissions need to reach every tool call

A shared agent may act for users with different permissions. The backend needs to decide whose credentials it uses and enforce that scope in tool calls, file access and environment allocation. Authenticating the initial HTTP request does not automatically carry those restrictions into every tool the agent invokes.

Authorization also changes independently of agent versions. A run can keep its original instructions while losing permission to use a connection. Pinning all settings at the start could preserve access an administrator meant to revoke. Reloading all settings on every step could change the instructions halfway through the task. Configuration and authorization need separate rules.

![A run keeps its selected configuration while tool calls check current authorization](configuration.svg "Figure 2. A run can retain its selected instructions while subsequent tool calls use current permissions.")

The same ownership rules apply to reusable environments. A sandbox or browser session can contain data from earlier work. Before reusing it, the backend has to know who owns that data and whether the next run may access it.

## Tracing behavior and measuring results

Infrastructure can report that a run completed even when the result is wrong. The agent may have received incomplete data, misunderstood a tool response or repeated an unproductive sequence of actions. A final answer and an HTTP status code do little to explain those failures.

### Traces need to be usable

An **execution trace** records the model requests, tool calls and child agents involved in a run. The useful evidence includes:

- the configuration and inputs used by the run;
- tool arguments and results, alongside the model's subsequent actions;
- errors, retries and execution attempts;
- time spent executing, queued or waiting for a person;
- token usage and attributable cost.

A harness can emit spans without providing the full investigation workflow. The spans still need to be stored and linked to the application's run and user records. A developer responding to a user report should be able to open that run and follow the evidence in a clear interface. Re-running the prompt is an unreliable substitute: the data, configuration or model's choices may have changed.

![A schematic run trace links model requests, a tool call and a child agent to the same execution](trace.svg "Figure 3. Individual operations belong to an inspectable run. Span widths are illustrative, not measured durations.")

Trace storage also falls under the application's data-access and retention rules. Tool outputs can contain the same private information the app handles elsewhere.

### Production data can guide improvements

Once usage and cost are attached to runs, the team can compare agent versions, investigate expensive tasks and find recurring tool failures. Aggregate statistics identify patterns; traces explain the individual cases behind them.

Execution data becomes more useful when the app adds feedback about the outcome. A completed run, an accepted result and a result someone had to correct are different observations. Recording those differences gives the team a basis for evaluating changes to instructions, skills or tools.

It also gives agents material to analyze. An agent can review failures across many users' runs, find common patterns and propose improvements, subject to the same access controls as any other analysis. The team can test those proposals against past failures and fresh tasks before releasing them. Shared execution records make that possible at a scale beyond one developer's test conversations.

## What managed agents take over

Much of this work is familiar backend engineering. A team may already have a job queue, an identity system and a tracing stack. Using them is sensible. The integration still has to connect accepted work to its permissions, configuration, execution state and results, including when a worker fails or a resource changes.

Managed agents provide a backend for that work. Applications call agents through an API, while the service handles some combination of execution, persistence, recovery, resource management and inspection. The benefit is having those responsibilities implemented and maintained together. The application team can spend less time wiring up the execution infrastructure and more time on the behavior and experience it wants to deliver.

The service's boundaries matter. It may bundle a particular harness, restrict the available environments, or impose an access model that doesn't fit the app. External side effects still need safe tool contracts. The application still owns its business rules and the criteria for a useful result. For a small app whose existing backend already covers the work, adopting another service may add little.

In a13n, Service provides the agent backend and runs on your infrastructure. Console is the frontend for managing and testing agents, inspecting execution traces, and reviewing usage and cost. Applications call those agents through an API or SDK. The [Service documentation](https://a13n.dev/docs/a13n-service/) covers that workflow and the deployment requirements.
