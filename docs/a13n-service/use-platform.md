---
title: Use an existing platform
description: Sign in to Console, try your team's agents, and create your own.
---

Use your team's agents through Console. You need the Console URL, an account, and permission to run an agent. To set up Service instead, start with the [quickstart](get-started.md). Console is separate from the Harness UI workbench.

## Sign in

1. Open the Console URL supplied by your administrator.
2. Sign in, or accept an invitation and create your account.
3. Select your team's workspace.

In the selected workspace, **runner** allows agent execution and **builder** also allows resource creation. Roles granted at the organization or workspace apply; ask an administrator for access if needed. See [Roles](identity.md#roles).

Console follows your browser's preferred language when it supports it; otherwise, it uses English. To choose a language, open your user menu and select **Preferences → Display language**. Choose **System** to follow the browser again.

## Try an agent

1. Open **Sessions → New session**, then **Choose an agent**.
2. Send a request that matches its purpose. For a general-purpose agent, try: “Turn these notes into two next steps: draft ready; review pending; release planned for Friday.”
3. Watch the response stream. The conversation can also show reasoning and tool calls.
4. Send a follow-up, such as “Make the first step more specific.” The same thread retains the discussion history.

To return later, open **Sessions** and select **Continue conversation** for that session. This opens Chat on its main thread; if there is no unique main thread, choose the history to open. A thread ID search keeps that exact target. Select **Inspect execution** or click the row to open Debug. You can switch between Chat and Debug in the conversation. If a run fails, inspect its details or share its ID with your administrator; see [troubleshooting](monitoring.md#troubleshoot-a-request-or-run).

Messages show the sender’s current name and original submission time in Chat and Debug. Your own messages have a **You** label; service accounts have a **Service account** label. Select a sender’s name to view and copy their email or account ID. Queued messages, guidance and inherited branch history retain their own senders. Names and emails reflect the current profile.

## Create a conversation branch

Choose **Create branch from here** below the response you want to continue from, or **Create branch** beside a run in Debug. Enter the new branch's first message and select **Create branch and send**. The branch stays in the same session and uses that run's agent version and options. Switch conversations from the header; the branch's origin link returns to the exact run it inherited.

The origin must have stopped executing, and you need permission to run agents. A waiting run can be branched without resolving its original wait; the new branch denies pending approvals and fails unanswered calls. Failed or cancelled runs continue from their last saved checkpoint.

File environments and memory are shared by default, so changes may be visible in both conversations. Under **Environment options**, **Use a new environment** uses the agent's default environment when configured, without copying old files. Memory remains shared.

## Create your own agent

With the builder or admin role in this workspace:

1. Confirm a model is available under **Models**, or [add one](get-started.md#add-a-model).
2. Open **Agents → Create manually**, choose the model, and add instructions such as “Turn project notes into short, concrete next steps.”
3. Select **Create agent**.
4. Choose **Try agent** and send the request from step 2 of [Try an agent](#try-an-agent).

Use **Save changes** after editing the configuration. New work uses the saved version by default; active work and approval continuations keep their original version. Messages pinned to a specific version keep that selection. Configuration changes create revisions, listed under **Versions**. Add [tools and connections](tools.md), [skills](skills.md), or [subagents](agents-and-runs.md#subagents) as needed. See [Agents](agents-and-runs.md#agents) for revision selection.

## Guide the work

- **Questions and approvals:** answer questions and choose **Approve once** or **Deny** for approvals, then select **Submit responses** after completing every pending item. The agent continues after the complete batch is accepted. An ordinary message does not resolve the wait.
- **Steer or stop:** send another message while a run is active, or stop the active run.
- **Files and commands:** select an environment under **Run options**, or configure an [environment template](environments.md#templates) as the agent's default.
- **Shared knowledge:** mount [memory](memory.md) on the thread or set it as an agent default. Memory can supply context automatically; enabled memory tools let the agent search and update it.

Read [Core concepts](../overview/core-concepts.md) for the conversation model, try [Agent Composer](agent-composer.md) for configuration help, or [connect your application](connect-application.md) to the same Service.
