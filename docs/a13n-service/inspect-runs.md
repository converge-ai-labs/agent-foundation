---
title: Inspect runs and improve agents
description: Inspect usage and trace evidence, then review findings before changing configuration.
---

Open the conversation in Console and switch to **Debug** to inspect its run status, saved configuration, attempts and usage. Those records do not require a trace backend. For model and tool spans or Finding Agent analysis, an operator must separately configure a [trace backend](configuration.md#logs-metrics-and-traces). Use the evidence below to diagnose the run before proposing changes.

## Usage

Use `GET /usage?session_id=...` for complete model totals, and `GET /usage/runs?session_id=...&limit=50` for a paginated run breakdown. Totals cover every matching record, independently of which runs the UI has loaded. Both endpoints also filter by run, thread and ingestion time.

`/usage` returns `{models:[...]}`. `/usage/runs` returns `{items:[{run_id,thread_id,run_number,models}],next_cursor}`. Follow the cursor with unchanged filters until null. Runs are oldest first, with all model groups for each run kept together. `run_number` counts all runs in that thread, including those without usage.

Each model total records requests, token counts and cost from saved pricing. Cached input is included in input tokens. Unattributed model keys and wholly unpriced costs are null. `average_time_to_first_chunk_seconds` averages eligible completed streamed requests. `output_tokens_per_second` divides inclusive output tokens by summed full request duration for completed positive-duration requests, including non-streaming requests and time before the first chunk. Each metric is null without its eligible observations.

A run's `usage_at_seal` captures usage when it ended. Late reports still enter `/usage`; time filters select their ingestion time.

## Traces

Configure a [trace backend](configuration.md#logs-metrics-and-traces), then open **Traces** in Console to investigate a slow, expensive or failed execution. Each attempt exports Harness spans, including inline children. Without a backend, trace queries return `503 unavailable`.

1. Select a time range.
2. Filter by run/thread/session or root attributes.
3. Apply duration limits in seconds or cost limits in USD, then sort by **Duration**, **Cost** or **Started**.
4. Open a trace and inspect the relevant spans and evidence.

For example, minimum duration `60` and minimum cost `0.01`, sorted by descending cost, finds reported traces lasting at least a minute and costing at least $0.01. Bounds are inclusive. Trace cost sums reported span costs; missing cost is unknown, and trace totals are separate from Service run accounting.

Use `/traces/search` with explicit `started_after`, `started_before`, `order_by` and `direction`. Keep filters unchanged when following its keyset cursor. Searches have bounded collection and a 30-second deadline; narrow the range or add an ID filter when limits are exceeded. Results depend on backend sampling, ingestion and retention, and pagination does not freeze changing data. The [API reference](api-reference/index.md) lists trace, span and attempt endpoints.

## Find and improve execution issues

Use **Insights → Findings** to turn trace evidence into a reviewed improvement:

1. Choose **Analyze traces**, select rules and a time range/count, and optionally add **Requirements for this analysis**. For one ended trace, use **Analyze trace**.
2. Open the analysis run from **Analysis history**. Read its summary, evidence and selection limits.
3. Open a finding, select **Edit assessment**, choose confirmed, expected, insufficient or false positive, add a **Review note**, and **Save assessment**.
4. For a finding worth changing, choose **Review with Composer**, send the proposed message, inspect the recommendation and approve a configuration write when appropriate.

Analysis requires a trace query backend and uses the built-in Finding Agent's selected default version and model. Selection scans at most 100 roots and chooses at most 20 traces. Execution/recovery rules prioritize failed runs and error/fatal roots within that scan. Counts show selection and citations, not proof that every selected trace was fully analyzed. Treat even critical findings as diagnoses to review against their evidence.

Categories distinguish unclear requests, instruction issues, tool design, tool usage, tool execution, answer quality, context gaps, workflow issues and boundary violations. They describe the issue rather than prove its cause. A normal permission denial is not a boundary violation; an unsupported success claim is an answer-quality issue.

Saved assessments and notes inform later analyses of the same agent revision. Changing the assessment clears the old rationale; closing or reopening preserves saved feedback. Each accepted analysis keeps its original input, so edits affect new analyses. Feedback is bounded and helps compare diagnoses without merging stored findings.

Finding Agent reads only selected trace evidence and can submit findings; it does not repair or publish configuration. External producers can use `POST /findings` with a stable `source_key` unique to their workspace/principal. They own trace/span references; Service validates cited runs and agent revisions.
