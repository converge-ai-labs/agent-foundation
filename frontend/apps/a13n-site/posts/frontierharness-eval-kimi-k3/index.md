---
title: a13n Harness on FrontierHarness Eval with Kimi K3
description: 21 of 30 tasks passed with Kimi K3. The selected results, reported cost per pass, and three task records from our local evaluation.
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

We ran a13n Harness with Kimi K3 on all 30 FrontierHarness Eval tasks. It passed **21 tasks (70.0%)**, at a reported standardized inference cost of **$2.93 per pass**. The median successful full trial took **7 minutes 3 seconds**, including setup and verification.

Cost per pass includes the reported fees of successful and failed selected tasks. Nine requests with unavailable usage, earlier excluded attempts, and machine costs sit outside that total. [Results and accounting](https://github.com/converge-ai-labs/agent-foundation/discussions/1064)

The frozen v1.0 task set combines 21 Terminal-Bench tasks and nine DeepSWE tasks. a13n passed **15/21** and **6/9**, respectively, using the frozen instructions, payloads, and verifiers. These are the selected FrontierHarness tasks, not full-suite scores. [Task set and methodology](https://github.com/frontier-harness-eval/eval/tree/e837a70bd6beb4e72eeeda62dd06e3bd34f6cb63)

## Pass rate and cost on 30 tasks

a13n’s local run had the highest observed pass rate beside the 12 frozen published configurations. The comparison is provisional: the baselines used Runta’s restored environments and Fireworks, while a13n used local OrbStack, different runner versions, and an internal model gateway. We ran no matched control, so the differences do not isolate the effect of the harness. [Frozen baseline data](https://github.com/frontier-harness-eval/eval/blob/e837a70bd6beb4e72eeeda62dd06e3bd34f6cb63/results/eval-data.json)

![Pass rate and reported standardized cost per pass for a13n and 12 frozen published configurations](comparison.svg "Figure 1. a13n is a local run; the other configurations are frozen published baselines. Runtime and provider conditions differ. Hollow points have partial reported cost coverage; the cost axis is logarithmic.")

## Three tasks behind the result

**SCC bounded-memory spilling passed in about 3,065 seconds.** a13n’s local run completed `datacurve/scc-bounded-memory-spilling`; none of the 12 configurations passed it in the frozen published results. [Per-task results](https://github.com/converge-ai-labs/agent-foundation/discussions/1064)

**Merge-diff stayed a failure after a later retry passed.** The original attempt exceeded the 900-second agent deadline, although a cleanup error masked the timeout in the raw runner status. We retained that first valid result and excluded the later passing retry.

**Meriyah kept its first valid clean rerun.** An earlier attempt obtained an upstream reference answer and was quarantined. After a setup-only failure, the clean rerun passed 48 of 49 fail-to-pass tests and all 51,469 pass-to-pass tests. The remaining failure involved `for-of` with `using` and `of` as the binding name. We retained the failure without selecting another quality retry.

The Meriyah rerun used an offline dependency cache, model-gateway-only agent egress, and a separate offline verifier. Other tasks kept their own task or suite policies. [Selection and clean-rerun audit](https://github.com/converge-ai-labs/agent-foundation/discussions/1064)

<details>
<summary>All 13 configurations</summary>

| Harness                 |    Passed | Cost per pass | Reported cost coverage |
| ----------------------- | --------: | ------------: | ---------------------: |
| **a13n Harness, local** | **21/30** |     **$2.93** |                 100%\* |
| Codex                   |     20/30 |         $3.47 |                   100% |
| Claude Code             |     19/30 |        $18.34 |                    97% |
| DSH Creator             |     19/30 |         $3.28 |                    90% |
| DSH PTC                 |     18/30 |         $4.58 |                    90% |
| DSH Standard            |     18/30 |         $3.46 |                    90% |
| Pi                      |     18/30 |         $2.43 |                   100% |
| DSH Minimal             |     17/30 |         $4.72 |                    87% |
| Kimi Code               |     17/30 |         $3.65 |                   100% |
| Oh My Pi                |     17/30 |         $4.75 |                   100% |
| Exo Harness             |     16/30 |         $1.05 |                   100% |
| Hermes                  |     15/30 |         $2.90 |                    93% |
| OpenCode                |     15/30 |         $3.24 |                    97% |

\*a13n’s 100% reported-cost coverage applies after the request exclusions. Complete raw usage is available for **22 of 30 tasks**. Baseline rows below 100% include only their available cost portion. Values are rounded here; the [evaluation report](https://github.com/converge-ai-labs/agent-foundation/discussions/1064) retains the detailed figures.

a13n passed one more task than the frozen Codex baseline, with a lower reported cost per pass. Its median successful trial took 7m 03s against Codex’s 6m 43s. Pi and Exo Harness reported lower costs per pass with fewer completed tasks.

</details>

## How cost per pass is calculated

We priced tokens with the frozen upstream [`kimi-k3-2026-08-20` table](https://github.com/frontier-harness-eval/eval/blob/e837a70bd6beb4e72eeeda62dd06e3bd34f6cb63/skills/frontierharness-eval/scripts/pricing.json): $3.00 per million fresh-input or cache-write tokens, $0.30 per million cache-read tokens, and $15.00 per million output tokens. These are standardized evaluation rates, not an actual supplier invoice.

First-cold normalization charges cached reads in each task’s first included root generation at the fresh-input rate. The adjustment raises the reported total from $60.91 to $61.43.

**$61.4332878 across all selected tasks, failures included ÷ 21 passes ≈ $2.93 per pass.**

The median normalized task cost was about $0.41. That describes the middle task; cost per pass spreads the reported fees across all 30 selected tasks over the 21 successes.

The fee includes **1,110 of 1,119 retained request records**. The nine excluded requests returned no response or usage; their provider charges are unknown, rather than zero. Earlier smoke tests, infrastructure-invalid attempts, quarantined attempts, and superseded retries are excluded from the selected-result total. [Detailed accounting](https://github.com/converge-ai-labs/agent-foundation/discussions/1064)

<details>
<summary>Execution conditions</summary>

| Setting           | Local a13n run                                                                                                                        |
| ----------------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| Foundation commit | [`c7e2871`](https://github.com/converge-ai-labs/agent-foundation/commit/c7e287142188dd581ef37daea8fdcfa59f310857)                     |
| Frozen benchmark  | FrontierHarness Eval v1.0 at [`e837a70`](https://github.com/frontier-harness-eval/eval/tree/e837a70bd6beb4e72eeeda62dd06e3bd34f6cb63) |
| Model access      | Kimi K3 through an internal OpenAI-compatible gateway; no explicit model-setting overrides                                            |
| Application       | Real Harness UI App with native file, shell, and image-reading tools                                                                  |
| Runners           | Harbor 0.23.0 for Terminal-Bench; Pier 0.3.1 for DeepSWE                                                                              |
| Host              | Apple Silicon/macOS with OrbStack; Linux amd64 task images under emulation                                                            |
| Evaluation dates  | October 15–16, 2026                                                                                                                   |

We used the general-purpose Harness UI instructions with the same fixed adapter guidance across tasks, without benchmark-specific instruction tuning to reduce tokens or inference cost. Each task instruction was submitted as the user task; the adapter preserved the agent loop. The upstream [reproduction workflow](https://github.com/frontier-harness-eval/eval/blob/e837a70bd6beb4e72eeeda62dd06e3bd34f6cb63/README.md) calls for a matched control to establish comparability.

</details>

## Run a13n

Use [Harness UI](https://a13n.dev/docs/a13n-harness-ui/) to work through a task interactively, or follow the [Harness documentation](https://a13n.dev/docs/a13n-harness/) to embed an agent in your Python application.

For results with GPT-6.1 Sol on a different task set, read [a13n Harness on Terminal-Bench 4.0](/blog/terminal-bench-4-benchmark/).

The [full evaluation report](https://github.com/converge-ai-labs/agent-foundation/discussions/1064) publishes the selected task outcomes, accounting, and artifact identifiers. Raw local execution artifacts remain private; the report is not a public reproduction package.
