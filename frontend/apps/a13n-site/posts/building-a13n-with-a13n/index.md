---
title: Building a13n with a13n
description: Open development, shared contracts, and the collaboration tools we improve through daily use.
date: '2026-11-05'
authors: [Zhongsheng Ji]
---

## Development principles

We follow two principles when developing a13n:

- Build in public and discuss in public.
- Work in a cycle of thinking, discussion, practice, and validation.

### Build in public, discuss in public

We use Issues and PRs to make problems, design discussions, action items, and implementation results public. The community can participate throughout: proposing needs, questioning designs, contributing information, implementing changes, and validating results.

Discussions also happen in offline syncs. We publish their conclusions and reasoning in Issues so developers and community members who were not present can follow progress and continue from the same information. Follow-up actions stay in the Issue, keeping discussion connected to the work.

### Think, discuss, practice, validate

Issues and PRs are the collaboration points in this cycle. Issues hold problem analysis, further discussion, and new action items; PRs hold implementation results and review. Thinking and discussion establish the goals, constraints, and responsibilities for an iteration. Practice and validation test those judgments.

Once a discussion reaches a conclusion, we assign the Issue to a specific person to carry it forward. They choose the tools and agents that suit their work, with uninterrupted time and room for independent judgment to reason, implement, and check within their responsibilities. When questions arise, they can continue the discussion in the Issue or talk in Slack or Lark.

Problems found during the work return to the Issue. If practice shows that an earlier judgment was wrong, we revisit the discussion and adjust the approach using the new facts. Each round of discussion should absorb what the work has taught us and lead to the next action.

Implementations go through PR review. A PR may be merged, with testing and real use continuing afterward, or sent back for further discussion in the Issue. Findings and new action items from validation feed the next round of thinking, discussion, and practice.

![The development cycle through Issues and PRs](development-cycle.svg "Figure 1. Community discussion and conclusions from offline syncs enter an Issue. A human assignee chooses tools and agents to implement and check the work, then submits a PR. Review can return a change to discussion; testing and use continue after merge and produce new findings and actions.")

## Specs and owners

We maintain code quality through specs and owners. Specs define system behavior, responsibilities, and boundaries. Owners make design judgments and review changes in their areas. People and agents work within these constraints.

The repository keeps accepted product and architecture designs in `spec/`, proposals and discussion in Issues, and implementation review in PRs. Changes to design contracts update the affected specs, implementation, tests, and documentation together, keeping the design basis aligned with actual behavior. Later contributors can use that foundation to understand, modify, and validate the system.

`MAINTAINERS.md` records maintainers by area of responsibility. Reviewers are selected by a change's intent and the architectural boundaries it affects, with file paths as supporting evidence. A change spanning several areas needs the relevant maintainers' review. When module boundaries change, ownership changes with them.

`DEVELOPMENT.md` sets our code quality expectations: use consistent domain concepts and clear responsibilities, give shared rules an explicit home, and keep execution paths direct so state, side effects, resource lifecycles, and failure handling are easy to follow. Introduce abstractions for current needs and reduce how much a maintainer must understand or change at once. Fix a problem where the rule belongs, then check the affected callers.

The same requirements apply to human-written and agent-generated code. A change earns its place by fitting the design, solving a real problem, and justifying its runtime and maintenance costs. AI does not replace those judgments. When assessing technical debt, we first examine the design trade-offs, the clarity of responsibilities, and whether someone continues to own the maintenance.

![How specs, owners, and change review fit together](spec-owner.svg "Figure 2. Specs give implementation and review a common basis. Owners review changes by responsibility and architectural boundary. When a design contract changes, the affected specs, code, tests, and documentation change together. Human-written and agent-generated code meet the same quality standard.")

## Build tools for collaboration, improve a13n through use

To collaborate better, we built our own bot with a13n and connected it to Lark, Slack, and GitHub. We use it in actual development, and that work helps us identify where a13n needs to improve.

We want a unified way to reach our project context. Discussions can happen in different places, but switching platforms should let us continue existing conversations and work without explaining the background again. Our bot connects project context across those discussions, while we keep improving a13n's context sharing and cross-thread communication so information can follow the work.[^shared-context]

We want an excellent user experience: a responsive main entry point, timely replies, and high-quality task results. That goal drives improvements to asynchronous execution and context management, including automatic background execution for long-running tools, receiving additional input during execution, and background context compaction. The main entry point stays available for communication while task agents get space to work and the context they need.[^responsive-entry]

We also want the system to grow with our projects and tasks and keep running over time. We therefore keep improving memory, environment management, and thread management: accumulating project knowledge, giving tasks explicit execution environments, and making work across threads possible to organize, follow up, and resume. These capabilities support the bot's daily collaboration and become reusable foundations in a13n.[^long-running]

![The bot's discussion entry points, task coordination, and shared project context](bot-architecture.svg "Figure 3. Lark, Slack, and GitHub host discussions. Each discussion's Main plans and coordinates its own Work threads. One project-level Core connects knowledge across Mains and maintains shared memory. Threads keep their own working context and collaborate through shared project knowledge.")

This is our dogfooding loop. Collaboration needs set the goals; actual use tests the design. Problems return to Issues for discussion, the relevant owners drive improvements, and PR review brings them back into use. The cycle of thinking, discussion, practice, and validation applies to our own tools and development process too.

![The feedback loop between collaboration needs, a13n improvements, and daily use](dogfooding-workflow.svg "Figure 4. Continuous context, timely responses, and long-running operation are the collaboration goals. Issues turn problems into discussion and actions. A human assignee chooses tools and agents to do the work, then submits it for owner review through a PR. The improved a13n returns to the bot and daily development, where use produces new evidence and questions.")

[^shared-context]: Shared memory: [PR #711](https://github.com/converge-ai-labs/agent-foundation/pull/711). Thread coordination: [PR #672](https://github.com/converge-ai-labs/agent-foundation/pull/672).

[^responsive-entry]: Async execution and steering: [Issue #1130](https://github.com/converge-ai-labs/agent-foundation/issues/1130), [PR #1140](https://github.com/converge-ai-labs/agent-foundation/pull/1140), [PR #1162](https://github.com/converge-ai-labs/agent-foundation/pull/1162). Background compaction: [PR #1241](https://github.com/converge-ai-labs/agent-foundation/pull/1241), [PR #1250](https://github.com/converge-ai-labs/agent-foundation/pull/1250).

[^long-running]: Memory budgets: [Issue #873](https://github.com/converge-ai-labs/agent-foundation/issues/873), [PR #874](https://github.com/converge-ai-labs/agent-foundation/pull/874). Environment lifecycles: [PR #584](https://github.com/converge-ai-labs/agent-foundation/pull/584). Thread resource reclamation: [PR #1339](https://github.com/converge-ai-labs/agent-foundation/pull/1339).
