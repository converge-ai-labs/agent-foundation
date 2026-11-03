---
title: a13n Harness on Terminal-Bench 4.0
description: A 62-task comparison with Codex, using GPT-6.1 Sol at maximum reasoning effort. Pass rates, inference costs, and the conditions behind the numbers.
date: '2026-11-03'
authors: [a13n team]
report:
  title: 'a13n Harness × Terminal-Bench 4.0: Comparable pass rate and inference cost'
  url: https://github.com/converge-ai-labs/agent-foundation/discussions/1015
  author:
    name: Zhongsheng Ji
    url: https://github.com/Wh1isper
    avatar: https://avatars.githubusercontent.com/u/43375501?u=fc30be3ffc549b9c2ceb82590c9e2782eeaeaeef&v=4&s=80
---

We ran a13n Harness on 62 Terminal-Bench 4.0 tasks with GPT-6.1 Sol at maximum reasoning effort. a13n passed **36 tasks (58.06%)**, compared with **58.77% for Codex 0.159.0**, recomputed from its published results on the same task set. The observed gap was about **0.7 percentage points**.

On the 61 tasks with complete official usage records, estimated inference cost was **$98.73 for a13n** and **$97.70 per Codex round**. a13n’s estimate uses the official cache-hit rate for each task. [Evaluation report](https://github.com/converge-ai-labs/agent-foundation/discussions/1015)

![a13n and Codex pass rates and inference costs](results.svg "Figure 1. Pass rate uses 62 shared task identities. Cost uses the 61 with complete official usage. a13n cost is estimated with official per-task cache-hit rates; costs exclude other attempts and machine costs.")

Costs cover selected or final runs and exclude other attempts and machine costs.

## Comparing the same 62 tasks

| Condition        | a13n Harness                                                   | Official Codex                          |
| ---------------- | -------------------------------------------------------------- | --------------------------------------- |
| Model and effort | GPT-6.1 Sol, `max`                                             | GPT-6.1 Sol, `max`                      |
| Task set         | 62 of Terminal-Bench 4.0.0’s 66 tasks                          | The same 62 tasks                       |
| Scored outcomes  | 36 passes and 26 failures                                      | 181 successes across 308 scored records |
| Retained runs    | One final outcome per task                                     | Five final records per task             |
| Build            | Development build based on `4eb06e8`, with local modifications | Codex 0.159.0                           |

Of the 310 official final records, 308 had valid scores. We excluded the two missing scores from Codex’s denominator while keeping all 62 task identities in scope. The unequal numbers of retained runs mean these observed results do not establish statistical equivalence.

## Why the cost comparison uses 61 tasks

One of the five official runs for `cumulative-layout-shift` has no usage record. We removed that task from both sides of the cost comparison. On the remaining 61 tasks, a13n’s estimate is **$1.03 higher per round**, about **1.1%**.

We kept a13n’s input and output token counts, applied the corresponding official task’s cache-hit rate, and summed the resulting fees. Cache behavior is an assumed input to this estimate; the figure is not an observed a13n invoice. [Cost methodology](https://github.com/converge-ai-labs/agent-foundation/discussions/1015)

Across all 62 tasks, the figures are $106.88 estimated for a13n and $102.53 of known official cost per round. Official usage is incomplete on that wider set, so those totals do not have equal coverage.

## Four tasks excluded by host resources

Terminal-Bench 4.0 contains 66 tasks. Our host had 12 CPU threads, about 32 GiB of memory, and no NVIDIA GPU meeting the excluded tasks’ requirements. These four tasks were removed from both sides:

| Excluded task           | Resource constraint                       |
| ----------------------- | ----------------------------------------- |
| `fp8-rmsnorm-gemm`      | Required GPU capability unavailable       |
| `jax-speedrun-gpu`      | Required GPU capability unavailable       |
| `math-eval-grader`      | Required GPU capability unavailable       |
| `live-database-cutover` | Requires 16 CPUs; the host had 12 threads |

## How the tasks ran

Harbor 0.23.0 prepared each Docker task container. The real Harness UI App drove the model with native file and shell tools, connecting task-declared Skills and MCP services where needed. Each task’s verifier decided pass or fail. The evaluated a13n build included local modifications beyond base commit `4eb06e8`.

The dataset was Terminal-Bench 4.0.0. Its resource, environment, instruction, and verifier changes make benchmark version part of the comparison. [Terminal-Bench 4.0 release notes](https://www.tbench.ai/news/terminal-bench-4-0)

## Run a13n

Use [Harness UI](https://a13n.dev/docs/a13n-harness-ui/) to work through a task interactively, or follow the [Harness documentation](https://a13n.dev/docs/a13n-harness/) to embed an agent in your Python application.

The [full evaluation report](https://github.com/converge-ai-labs/agent-foundation/discussions/1015) contains the results, exclusions, and accounting. For a different model and task set, see [a13n Harness on FrontierHarness Eval with Kimi K3](/blog/frontierharness-eval-kimi-k3/).
