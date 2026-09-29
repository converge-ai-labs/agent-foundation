---
title: Use an existing platform
description: Sign in to Console, try your team's agents, and create your own.
---

Use your team's agents through Service Console. You need the Console URL, an account, and permission to run an agent. To set up Service instead, start with the [quickstart](get-started.md). Console is separate from the Harness UI workbench.

## Sign in

1. Open the Console URL supplied by your administrator.
2. Sign in, or accept an invitation and create your account.
3. Select your team's workspace.

In the selected workspace, **runner** allows agent execution and **builder** also allows resource creation. Roles granted at the organization or workspace apply; ask an administrator for access if needed. See [Roles](identity.md#roles).

## Try an agent

1. Open **Sessions → New session**, then **Choose an agent**.
2. Send a request that matches its purpose. For a basic assistant, try: “Turn these notes into two next steps: draft ready; review pending; release planned for Friday.”
3. Watch the response stream. The conversation can also show reasoning and tool calls.
4. Send a follow-up, such as “Make the first step more specific.” The same thread retains the discussion history.

Return to the saved conversation later to continue. If a run fails, inspect its details or share its ID with your administrator; see [troubleshooting](monitoring.md#troubleshoot-a-request-or-run).

## Create your own agent

With the builder or admin role in this workspace:

1. Confirm a model is available under **Models**, or [add one](get-started.md#add-a-model).
2. Open **Agents → Create agent**, choose the model, and add instructions such as “Turn project notes into short, concrete next steps.”
3. Save it, choose **Try agent**, and send the request above.

Each save creates a revision. Add [tools and connections](tools.md), [skills](skills.md), or [subagents](agents-and-runs.md#subagents) as needed. See [Agents](agents-and-runs.md#agents) for revision selection.

## Guide the work

- **Questions and approvals:** answer in the conversation; approve or reject requested tool calls.
- **Steer or stop:** send another message while work is running, or stop the active run.
- **Files and commands:** select an environment under **Run options**, or configure an [environment template](environments.md#templates) as the agent's default.
- **Shared knowledge:** attach [memory](memory.md) to the thread or set it as an agent default. Memory can supply context automatically; enabled memory tools let the agent search and update it.

Read [Core concepts](../overview/core-concepts.md) for the conversation model, try [Agent Composer](agent-composer.md) for configuration help, or [connect your application](connect-application.md) to the same Service.
