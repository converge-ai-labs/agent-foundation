"""Small, rebuildable continuation projections; never execution authority."""

from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ThinkingPart, ToolCallPart, ToolReturnPart

from a13n_harness_ui.goal import saved_goal

from .contracts import RetainedActivity, StoredContinuation, ThreadReadModel


def project_continuation(value: StoredContinuation) -> ThreadReadModel:
    goal = saved_goal(value.harness_state)
    activity = None
    for message in reversed(value.harness_state.message_history):
        for part in reversed(message.parts):
            if isinstance(message, ModelResponse):
                if isinstance(part, (TextPart, ThinkingPart)) and part.content.strip():
                    activity = RetainedActivity(
                        kind="assistant" if isinstance(part, TextPart) else "reasoning",
                        text=part.content.strip()[:2048],
                        occurred_at=message.timestamp,
                    )
                elif isinstance(part, ToolCallPart):
                    activity = RetainedActivity(kind="tool", text=part.tool_name[:2048], occurred_at=message.timestamp)
            elif isinstance(message, ModelRequest) and isinstance(part, ToolReturnPart):
                activity = RetainedActivity(kind="tool", text=part.tool_name[:2048], occurred_at=message.timestamp)
            if activity is not None:
                return ThreadReadModel(deferred_requests=value.deferred_requests, latest_activity=activity, goal=goal)
    return ThreadReadModel(deferred_requests=value.deferred_requests, goal=goal)
