---
title: Agents and conversations
description: Find where to configure an agent, inspect a run, and retain conversation state in each product.
---

Service and Harness UI both run agents with Harness. The host determines where configuration and conversation history live, who can access them, and how work resumes.

## Choose an agent

In Service, select an [agent in your workspace](../a13n-service/core-concepts.md#choose-an-agent). Harness UI loads Agents from its [instance configuration](../a13n-harness-ui/configuration.md). With embedded Harness, [construct the Agent in your Python process](../a13n-harness/agents-and-runs.md).

## Start a conversation

Send a message to Service from [Console or your application](../a13n-service/core-concepts.md#start-a-conversation). In Harness UI, the terminal and [WebUI](../a13n-harness-ui/webui.md) share a workbench. With Harness, your application supplies the input and delivers the output.

## Answer a question or approve a tool call

Service exposes [pending requests](../a13n-service/core-concepts.md#answer-a-question-or-approve-a-tool-call) to the caller or Console user. When embedding Harness, implement the [interaction handler](../a13n-harness/human-in-the-loop.md) and the [tool approval policy](../a13n-harness/managed-tools.md) for your host.

## Continue later

Service saves [thread history](../a13n-service/core-concepts.md#continue-later) in the deployment. Harness UI persists conversations in its instance. With embedded Harness, choose how your host [stores state and resumes work](../a13n-harness/state-and-resume.md).

## Understand execution when you need it

Service groups threads in a session and records [runs and attempts](../a13n-service/core-concepts.md#understand-execution-when-you-need-it) for each conversation. Embedded applications can [observe Harness execution](../a13n-harness/observation.md) directly.
