---
title: Use an existing platform
description: Run a team agent in Console, continue the conversation, and create your own agent.
---

Use this guide when your team already runs Service. You need its Console URL, an account, and permission to run an agent. You will send a request, follow up in the same thread, and learn where to inspect or branch the work. To deploy Service yourself, start with the [quickstart](get-started.md). Console is the Service application; Harness UI is a separate workbench.

## Sign in

1. Open the Console URL supplied by your administrator.
2. Sign in, or accept an invitation and create your account.
3. Select your team's workspace.

A **runner** can execute agents; a **builder** can also create resources. Organization and workspace grants apply to the selected workspace. Ask an administrator for the access you need; see [Roles](identity.md#roles).

To change the display language, open your user menu and select **Language → Display language**. **System** follows a supported browser language and falls back to English. The choice is saved in this browser.

## Try an agent

1. Open **Sessions → New session**, then **Choose an agent**.
2. Send a request that fits its purpose. For a general-purpose agent, try: `Turn these notes into two next steps: draft ready; review pending; release planned for Friday.`
3. Read the streamed reply. Tool calls and reasoning can appear alongside it.
4. Follow up with `Make the first step more specific.` The same thread retains the discussion history.

To return later, open **Sessions** and select **Continue thread**. When a session has several possible main threads, choose one; searching by thread ID opens that exact target. Use Chat to continue the conversation and Debug to inspect execution.

## Create a conversation branch

Branch when you want to explore another direction while keeping the original conversation:

1. Choose **Create branch from here** below a response, or **Create branch** beside a run in Debug.
2. Enter the new branch's first message, such as `Explore a plan that postpones the release by one week.`
3. Check the environment choice, then select **Create branch and send**.

The branch stays in the same session and uses the selected run's agent version and options. Switch threads from the header; the origin link returns to the exact run whose history the branch inherited.

You need run permission, and the selected run must have stopped executing. A waiting run can be branched: the new branch denies its pending approvals and fails unanswered calls, while the original keeps waiting. A failed or cancelled run supplies its last saved checkpoint, so unsaved work may be absent.

By default, both threads share file environments and memory. To start with a fresh file environment, select **Environment options → Use a new environment**. This uses the agent's default environment if configured, copies no old files, and keeps memory shared.

## Create your own agent

With builder or admin access, use [Agent Composer](agent-composer.md) to describe your agent and review its proposed configuration. For manual setup:

1. Check that **Models** has a usable model, or [add one](get-started.md#add-a-model).
2. Open **Agents → Create manually**, name the agent, choose the model, and enter `Turn project notes into short, concrete next steps.` as its instructions.
3. Select **Create agent**, then **Try agent**.
4. Send the notes from [Try an agent](#try-an-agent), read the reply, and adjust the instructions if needed.

After editing, select **Save changes**. Each configuration change creates a revision under **Versions**. New work uses the saved version by default; active work and approval continuations retain their original version, and explicitly pinned messages keep their selection. See [revision selection](agents-and-runs.md#agents) before changing an agent used by an application.

Add [tools](tools.md), [skills](skills.md), or [subagents](agents-and-runs.md#subagents) as the task grows.

## Guide the work

| Situation                                     | Action                                                                                                                                                                                                                               |
| --------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| The agent asks questions or requests approval | Answer each question and choose **Approve once** or **Deny** for each approval. Complete every pending item, then select **Submit responses**. The accepted batch continues the work; an ordinary message does not resolve the wait. |
| You need to redirect ongoing work             | Send another message while the run is active, or stop the active run.                                                                                                                                                                |
| The task needs files or commands              | Select an environment under **Run options**, or set an [environment template](environments.md#templates) as the agent's default.                                                                                                     |
| Several conversations need the same knowledge | Select [Memory Spaces](memory.md#memory-spaces) in the agent defaults or session **Memory** control. Use [standalone mounts](memory.md#mount-a-memory-on-a-thread) for existing file stores or record memories.                      |

## Inspect a reply

Select **Inspect execution** in Sessions, or switch the conversation to Debug. Start with the question you need to answer:

| Question                                 | Where to look                                                                                                                                                                                                                         |
| ---------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Why did the run fail?                    | Open its details and inspect the failure. Share the run ID with your administrator when needed; see [troubleshooting](monitoring.md#troubleshoot-a-request-or-run).                                                                   |
| Which configuration produced this reply? | Select the model in Chat, or **Agent** or **Configuration** in Debug. The read-only view combines that run's agent version with its overrides; changed sections are marked **Overridden**. Later agent edits do not change this view. |
| What did the conversation cost?          | Select the session's cost in the Debug header for model and run breakdowns. A run's **Details** shows its own usage, configuration, attempts, and lineage.                                                                            |
| Who sent a message?                      | Select the sender's name to view or copy their email or account ID. Messages show their original submission time and the sender's current profile. Your messages carry **You**; service accounts carry **Service account**.           |

Usage includes input, cached input, cache hit rate, output, time to first token (TTFT), output speed, and cost. Cached input is part of input. Expand a model call for its own timing figures. Each reply also identifies its model and override count. Queued messages, guidance, and inherited branch history keep their own senders.

Next, use [Agent Composer](agent-composer.md) to refine the agent, [connect your application](connect-application.md) to it, or read [Core concepts](../overview/core-concepts.md) for the conversation model.
