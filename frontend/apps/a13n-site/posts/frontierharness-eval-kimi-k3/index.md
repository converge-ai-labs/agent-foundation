---
title: a13n Harness on FrontierHarness Eval with Kimi K3
description: 21 of 30 selected tasks passed. Two views of inference cost, task records, and the timing limits of this local comparison.
date: '2026-11-03'
authors: [a13n team]
report:
  title: 'a13n Harness × FrontierHarness Eval: 21/30 tasks with Kimi K3'
  url: https://github.com/converge-ai-labs/agent-foundation/discussions/1064
  author:
    name: Zhongsheng Ji
    url: https://github.com/Wh1isper
    avatar: https://avatars.githubusercontent.com/u/43375501?u=fc30be3ffc549b9c2ceb82590c9e2782eeaeaeef&v=4&s=80
---

We ran a13n Harness with Kimi K3 on all 30 FrontierHarness Eval tasks. It passed **21 tasks (70.0%)**. The reported median task cost was **$0.41**, and the reported total divided by passes was **$2.93**. Complete raw usage is available for **22 of the 30 tasks**. [Evaluation report](https://github.com/converge-ai-labs/agent-foundation/discussions/1064)

The result has an unresolved timing comparison: the public records do not establish equal DeepSWE time limits between this local run and the published baselines. We present the observed outcomes without assigning a leaderboard rank.

The frozen v1.0 set combines **21 tasks from Terminal-Bench 2.1** and **nine from DeepSWE v1.1**. a13n passed **15/21** and **6/9**, respectively. This is a selected task set, distinct from the Terminal-Bench 4.0 evaluation in our other report. [Frozen benchmark manifest](https://github.com/frontier-harness-eval/eval/blob/e837a70bd6beb4e72eeeda62dd06e3bd34f6cb63/benchmark.json)

## A timing question behind the score

In the frozen baseline data, **20 DeepSWE failures have a duration of exactly 3,600 seconds**; all other recorded baseline trials finish below that value. This pattern is consistent with a one-hour cutoff, but the durations alone do not establish which execution phases that cutoff covers. The upstream methodology says a cell watchdog records its configured limit. [Baseline records](https://github.com/frontier-harness-eval/eval/blob/e837a70bd6beb4e72eeeda62dd06e3bd34f6cb63/results/eval-data.json) · [Timing definition](https://github.com/frontier-harness-eval/eval/blob/e837a70bd6beb4e72eeeda62dd06e3bd34f6cb63/skills/frontierharness-eval/SKILL.md)

Three local a13n DeepSWE trials lasted longer than 3,600 seconds:

| Task                              | Retained result | Full-trial time |
| --------------------------------- | --------------- | --------------: |
| Arktype JSON schema refs          | Pass            |       4,902.6 s |
| Meriyah resource declarations     | Fail            |       4,834.6 s |
| Python statemachine state scoping | Fail            |       5,555.7 s |

These local times include setup and verification on Apple Silicon with amd64 emulation. The public report does not provide the phase timings and deadline configuration needed to reconcile them with the baseline. Emulation may explain some overhead; it does not establish an equivalent agent time budget.

As a sensitivity check, counting the Arktype pass as a failure would give **20/30 (66.7%)**, equal to the frozen Codex pass count. Keeping the reported $61.4333 total unchanged would give **$3.07 per pass**. This is a hypothetical recount, not a rerun or a verified timeout correction. Resolving the deadline and phase timings is necessary before attributing the one-task difference to the harness.

## Pass rate and two views of cost

The chart uses the **median cost per task**, including successful and failed tasks. a13n’s reported median is $0.4137; the frozen Codex median is $0.2305. Eleven of the twelve published baseline configurations have a lower median than a13n’s reported value, although coverage and execution conditions differ.

![Pass rate and reported median cost per task for a13n and 12 frozen published configurations](comparison.svg "Figure 1. Median cost per task uses available first-cold task costs, including failures. a13n is an unranked local run with incomplete usage and unresolved timing comparability. Hollow baseline points have missing task costs. The cost axis is logarithmic.")

The table keeps **cost per pass** alongside the median because it answers a different question: how the total reported cost, including failed tasks, is spread across the passes.

<details>
<summary>All 13 configurations and cost coverage</summary>

| Harness                 |    Passed | Median / task | Cost / pass | Cost data                     |
| ----------------------- | --------: | ------------: | ----------: | ----------------------------- |
| **a13n Harness, local** | **21/30** |   **$0.4137** | **$2.9254** | 30 estimates; **22 complete** |
| Codex                   |     20/30 |       $0.2305 |     $3.4683 | 30/30 tasks                   |
| Claude Code             |     19/30 |       $0.3941 |    $18.3368 | 29/30 tasks                   |
| DSH Creator             |     19/30 |       $0.1697 |     $3.2849 | 27/30 tasks                   |
| DSH PTC                 |     18/30 |       $0.1648 |     $4.5774 | 27/30 tasks                   |
| DSH Standard            |     18/30 |       $0.3088 |     $3.4589 | 27/30 tasks                   |
| Pi                      |     18/30 |       $0.2347 |     $2.4330 | 30/30 tasks                   |
| DSH Minimal             |     17/30 |       $0.3385 |     $4.7180 | 26/30 tasks                   |
| Kimi Code               |     17/30 |       $0.2564 |     $3.6471 | 30/30 tasks                   |
| Oh My Pi                |     17/30 |       $0.2392 |     $4.7455 | 30/30 tasks                   |
| Exo Harness             |     16/30 |       $0.3142 |     $1.0452 | 30/30 tasks                   |
| Hermes                  |     15/30 |       $0.4331 |     $2.9043 | 28/30 tasks                   |
| OpenCode                |     15/30 |       $0.0870 |     $3.2443 | 29/30 tasks                   |

Baseline medians are recomputed from non-null canonical `cost_first_cold_usd` values in the frozen data, including failures. Missing task costs are excluded from the median. a13n’s median comes from the report’s 30 task estimates, eight of which have incomplete raw usage. Its total and cost per pass exclude nine requests with unknown charges. Partial figures describe available usage, so their completeness differs from fully measured rows.

Pi, Exo Harness, and Hermes report lower costs per pass than a13n, with fewer passes. Hermes has cost data for 28 of 30 tasks. The local run’s median successful full trial was 7m 03s; Codex’s was 6m 43s. These timings include setup and verification.

The frozen upstream guidance contains conflicting cost-label conventions, including a later website-alignment rule based on total cost divided by passes. Here, **median** always means the statistical task median, and **cost per pass** always means reported total divided by passes. [Upstream guidance](https://github.com/frontier-harness-eval/eval/blob/e837a70bd6beb4e72eeeda62dd06e3bd34f6cb63/skills/frontierharness-eval/SKILL.md)

</details>

Most of the reported cost came from the nine DeepSWE tasks. The direction of the cost difference changes across the two subsets:

| Selected subset              | a13n reported cost | Frozen Codex cost |
| ---------------------------- | -----------------: | ----------------: |
| DeepSWE, 9 tasks             |             $54.80 |            $64.51 |
| Terminal-Bench 2.1, 21 tasks |              $6.63 |             $4.86 |

The baselines used Runta’s restored environments and Fireworks. a13n used local OrbStack, different runner versions, and the `bailian-kimi-k3` route through an internal gateway. No matched control was run. These differences also affect speed, caching, and cost.

## Three retained task records

**SCC bounded-memory spilling passed in about 3,065 seconds.** a13n completed `datacurve/scc-bounded-memory-spilling`; none of the 12 configurations passed it in the frozen published results. The timing and environment qualifications above still apply. [Per-task results](https://github.com/converge-ai-labs/agent-foundation/discussions/1064)

**Merge-diff stayed a failure after a later retry passed.** The original attempt exceeded the 900-second agent deadline, although a cleanup error masked the timeout in the raw runner status. We retained that first valid result and excluded the later passing retry.

**Meriyah kept its first valid clean rerun.** An earlier attempt obtained an upstream reference answer and was quarantined. After a setup-only failure, the clean rerun passed 48 of 49 fail-to-pass tests and all 51,469 pass-to-pass tests. The remaining failure involved `for-of` with `using` and `of` as the binding name. We retained the failure without selecting another quality retry.

The Meriyah rerun used an offline dependency cache, model-gateway-only agent egress, and a separate offline verifier. Other tasks kept their own task or suite policies. [Selection and clean-rerun audit](https://github.com/converge-ai-labs/agent-foundation/discussions/1064)

## How the reported costs are calculated

We priced tokens with the frozen upstream [`kimi-k3-2026-08-20` table](https://github.com/frontier-harness-eval/eval/blob/e837a70bd6beb4e72eeeda62dd06e3bd34f6cb63/skills/frontierharness-eval/scripts/pricing.json): $3.00 per million fresh-input or cache-write tokens, $0.30 per million cache-read tokens, and $15.00 per million output tokens. These are standardized evaluation rates, rather than an actual supplier invoice.

First-cold normalization charges cached reads in each task’s first included root generation at the fresh-input rate. It raises the reported total from $60.91 to **$61.4332878**. Dividing by 21 passes gives **$2.9254 per pass**; taking the median of the 30 reported task estimates gives **$0.4137 per task**.

The fee includes **1,110 of 1,119 retained request records**. Nine requests returned no response or usage, so their charges are unknown. Earlier smoke tests, infrastructure-invalid attempts, quarantined attempts, superseded retries, and machine costs are excluded. Complete raw usage is available for **22/30 tasks**; the published totals account for available usage across all 30. [Detailed accounting](https://github.com/converge-ai-labs/agent-foundation/discussions/1064)

<details>
<summary>Execution conditions and reproduction limits</summary>

| Setting           | Local a13n run                                                                                                                        |
| ----------------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| Foundation commit | [`c7e2871`](https://github.com/converge-ai-labs/agent-foundation/commit/c7e287142188dd581ef37daea8fdcfa59f310857)                     |
| Frozen benchmark  | FrontierHarness Eval v1.0 at [`e837a70`](https://github.com/frontier-harness-eval/eval/tree/e837a70bd6beb4e72eeeda62dd06e3bd34f6cb63) |
| Task sources      | Terminal-Bench 2.1 selection; DeepSWE v1.1 selection                                                                                  |
| Model access      | Kimi K3 via `bailian-kimi-k3` through an internal OpenAI-compatible gateway; no explicit model-setting overrides                      |
| Application       | Real Harness UI App with native file, shell, and image-reading tools                                                                  |
| Runners           | Harbor 0.23.0 for Terminal-Bench; Pier 0.3.1 for DeepSWE                                                                              |
| Host              | Apple Silicon/macOS with OrbStack; Linux amd64 task images under emulation                                                            |
| Evaluation dates  | October 15–16, 2026                                                                                                                   |

We used the general-purpose Harness UI instructions with the same fixed adapter guidance across tasks, without benchmark-specific instruction tuning to reduce tokens or inference cost. Each task instruction was submitted as the user task; the adapter preserved the agent loop. The upstream [reproduction workflow](https://github.com/frontier-harness-eval/eval/blob/e837a70bd6beb4e72eeeda62dd06e3bd34f6cb63/README.md) calls for a matched control to establish comparability.

The public report includes selected outcomes, accounting, and artifact identifiers. Raw local execution artifacts remain private, and the timing evidence needed for a matched comparison is not published. It is not a public reproduction package.

</details>

## Run a13n

Use [Harness UI](https://a13n.dev/docs/a13n-harness-ui/) to work through a task interactively, or follow the [Harness documentation](https://a13n.dev/docs/a13n-harness/) to embed an agent in your Python application.

For GPT-6.1 Sol on a different benchmark version, read [a13n Harness on Terminal-Bench 4.0](/blog/terminal-bench-4-benchmark/).
