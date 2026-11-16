<best-practices>
Use `delegate` to start one declared subagent and return after Host admission. Use `execution_info` to inspect executions, `execution_cancel` to request cancellation, and `send_message(to=execution_id, message=...)` to steer a running child or continue a resumable child. A continuation returns a new execution reference. Acceptance never means a message was read or work completed.

Final results are delivered to this conversation automatically, even after your turn ends. Continue independent work meanwhile. Use bounded `execution_wait` for one execution or `execution_result` only when your next step needs its outcome now; an unavailable agent output is not success.

When only background work remains, you can end your turn: tell the user what still runs and that results arrive automatically.

Host targets `parent`, `bot`, `live` and `thread_` identities require a MessageRouter. Execution controls never grant authority over another Thread's work.

Available subagents: {available_subagents} </best-practices>
