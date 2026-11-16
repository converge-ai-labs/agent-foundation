<essential-rules>
Cross-Thread work creates independent roots. Creation receipts mean admission only; messages persist. Inspect saved outcomes before reporting completion; reconcile uncertain sends before retrying. Do not create acknowledgement loops or pass a task back to its requester.
</essential-rules>

<!-- reference -->

<best-practices>
Use get_thread() to inspect saved history and status; current_run describes the captured configuration, while thread.configuration describes next-Run selections. The configuration inspection exposes the target's next_model_id and captured Model separately. list_projects, list_agents and list_models discover accepted resources, not proven model connectivity. Cross-Thread work creates independent root conversations, not delegated child executions. create_thread returns admission, not completed work. Inspect progress with get_thread. Use send_message(to=thread_id, message=...) for questions, replies and results: the inbox steers active work, starts idle Runs, or holds paused, preparing or decision-blocked input. Incoming tasks and messages identify the source Thread; target that ID to answer. Sending does not wait for a reply. The receipt {accepted: true, input_id} means persisted, not read or processed. A rejected or uncertain send must be reconciled, not blindly retried. Do not create acknowledgement loops or delegate a task back to its requester.
</best-practices>
