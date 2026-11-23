# Usage and costs

Start with `usage_overview` for a bounded `start`/`end` window and an explicit timezone. Use `usage_agents`, `usage_models`, `list_usage_members` and `list_session_usage` for breakdowns; member breakdowns and principal filters require admin. Attribution identifies the execution principal, including shared service accounts, not necessarily a person who sent a message. Read tool schemas for required fields and limits.

`summarize_usage` and `list_run_usage` answer Run, Thread and Session questions. Follow pagination without changing filters; a page sum is not the workspace total. Costs are recorded USD subtotals, not invoices or environment costs. Always report `unpriced_requests`; missing pricing is not zero cost. Cached input is included in input tokens. Usage follows first ingestion time; Run metrics follow Run start time and average duration covers sealed Runs.

Use `context_usage` for the selected main model generation and configured capacity, not cumulative tokens or a pending prompt estimate. `model_request_metrics` reads latency/throughput for one recorded contribution. These queries use stored usage, not reconstructed traces. Null or unavailable evidence must remain unknown.
