"""Hand-authored upstream responses, independent of Service serializers."""

import json


def sse(events, *, named=False):
    return "".join(
        (f"event: {event['type']}\n" if named else "") + f"data: {json.dumps(event)}\n\n" for event in events
    )


def chat(body, answer, tool=None):
    message = {"role": "assistant", "content": None if tool else answer}
    if tool:
        message["tool_calls"] = [tool]
    finish = "tool_calls" if tool else "stop"
    result = {
        "id": "chatcmpl_fixture",
        "object": "chat.completion",
        "created": 1,
        "model": body["model"],
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": {"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25},
    }
    if not body.get("stream"):
        return result
    delta = {"role": "assistant"}
    if tool:
        delta["tool_calls"] = [{"index": 0, **tool}]
    else:
        delta["content"] = answer
    base = {key: value for key, value in result.items() if key not in {"choices", "usage"}}
    base["object"] = "chat.completion.chunk"
    return (
        sse(
            [
                {**base, "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": finish}], "usage": result["usage"]},
            ]
        )
        + "data: [DONE]\n\n"
    )


def responses(body, answer, tool=None):
    item = {
        "id": "msg_fixture",
        "type": "message",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": answer, "annotations": []}],
    }
    if tool:
        item = {
            "id": "fc_fixture",
            "type": "function_call",
            "call_id": tool["id"],
            **tool["function"],
            "status": "completed",
        }
    result = {
        "id": "resp_fixture",
        "object": "response",
        "created_at": 1,
        "status": "completed",
        "model": body["model"],
        "output": [item],
        "usage": {"input_tokens": 20, "output_tokens": 5, "total_tokens": 25},
    }
    if not body.get("stream"):
        return result
    events = [
        {"type": "response.created", "response": {**result, "status": "in_progress", "output": []}},
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {**item, "status": "in_progress", **({"arguments": ""} if tool else {"content": []})},
        },
    ]
    if tool:
        events.append(
            {
                "type": "response.function_call_arguments.delta",
                "output_index": 0,
                "item_id": item["id"],
                "delta": item["arguments"],
            }
        )
    else:
        events.extend(
            [
                {
                    "type": "response.content_part.added",
                    "output_index": 0,
                    "content_index": 0,
                    "item_id": item["id"],
                    "part": {"type": "output_text", "text": "", "annotations": []},
                },
                {
                    "type": "response.output_text.delta",
                    "output_index": 0,
                    "content_index": 0,
                    "item_id": item["id"],
                    "delta": answer,
                },
            ]
        )
    events.extend(
        [
            {"type": "response.output_item.done", "output_index": 0, "item": item},
            {"type": "response.completed", "response": result},
        ]
    )
    return sse([{**event, "sequence_number": index} for index, event in enumerate(events)], named=True)


def anthropic(body, answer):
    message = {
        "id": "msg_fixture",
        "type": "message",
        "role": "assistant",
        "model": body["model"],
        "content": [{"type": "text", "text": answer}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 20, "output_tokens": 5},
    }
    if not body.get("stream"):
        return message
    return sse(
        [
            {"type": "message_start", "message": {**message, "content": [], "stop_reason": None}},
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": answer}},
            {"type": "content_block_stop", "index": 0},
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": 5},
            },
            {"type": "message_stop"},
        ],
        named=True,
    )


def google(answer, streaming):
    result = {
        "candidates": [{"index": 0, "content": {"role": "model", "parts": [{"text": answer}]}, "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": 20, "candidatesTokenCount": 5, "totalTokenCount": 25},
        "modelVersion": "gemini-2.5-flash",
    }
    return sse([result]) if streaming else result
