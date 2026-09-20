"""Feishu/Lark confirmation cards with requester-bound action values."""

import json
from functools import cache
from importlib.resources import files
from zoneinfo import ZoneInfo

from pydantic import JsonValue

from a13n_service.connectivity.domain import JsonObject
from a13n_service.temporal import assume_utc, utc_now

from .domain import RoutineDefinition
from .models import RoutineRecord
from .service import proposal_arguments


@cache
def _labels(language: str) -> dict[str, str]:
    data = json.loads(files(__package__).joinpath("card_labels.json").read_text(encoding="utf-8"))
    return data[language if language in data else "en_us"]


def render(row: RoutineRecord, *, language: str = "en_us") -> JsonObject:
    labels = _labels(language)
    proposal = proposal_arguments(row) if row.proposal_json else None
    definition = (
        proposal.definition
        if proposal and proposal.definition
        else (RoutineDefinition.model_validate(row.definition_json) if row.definition_json else None)
    )
    state = labels[f"confirm_{proposal.operation}" if proposal else row.state]
    title = definition.title if definition else labels["title"]
    event = definition.event if definition else None
    if event:
        if proposal and proposal.operation == "save":
            state = labels["confirm_event"]
        elif not proposal and row.state == "active":
            state = labels["event_queued" if row.next_run_at else "watching"]
        elif not proposal and row.state == "completed":
            state = labels["event_completed"]
    details = [state]
    schedule = definition.schedule if definition else None
    if schedule:
        details.append(schedule.describe())
    if event:
        details.append(labels["event_condition"].format(event=labels.get(event.event_type, event.event_type)))
        conditions = ", ".join(
            f"{labels.get('filter_' + key, key)}: {value}" for key, value in event.filters.items() if value is not None
        )
        details.append(labels["event_filters"].format(filters=conditions or labels["all_events"]))
        details.append(labels["event_once" if event.once else "event_ongoing"])
        source = row.proposal_json.get("_event_source") if row.proposal_json else None
        if isinstance(source, dict):
            details.append(
                labels["event_source"].format(
                    account=source["account_name"],
                    kind=labels.get(str(source["target_kind"]), source["target_kind"]),
                    target=source["external_target_id"],
                )
            )
        details.append(labels["event_source_id"].format(id=event.source_target_id))
        details.append(labels["event_sharing"])
    if definition:
        details.append(definition.prompt)
    details.append(labels["destination"].format(owner=row.owner_id))
    next_at = schedule.next_after(utc_now()) if proposal and schedule else row.next_run_at
    if next_at and schedule:
        zone = ZoneInfo(schedule.timezone)
        details.append(
            labels["next"].format(time=f"{assume_utc(next_at).astimezone(zone):%Y-%m-%d %H:%M} ({zone.key})")
        )
    elif proposal and schedule:
        details.append(labels["expired"])
    if proposal:
        details.append(labels["confirmation"])
    if row.last_error:
        details.append(labels["error"].format(error=row.last_error))
    # Plain text prevents a task prompt from injecting links, mentions, or card formatting.
    elements: list[JsonValue] = [{"tag": "div", "text": {"tag": "plain_text", "content": detail}} for detail in details]
    operations = (
        ("confirm", "cancel")
        if proposal
        else ("pause", "delete")
        if row.state == "active"
        else ("resume", "delete")
        if row.state == "paused"
        else ()
    )
    actions: list[JsonValue] = []
    for operation in operations:
        button: JsonObject = {
            "tag": "button",
            "type": "primary" if operation == "confirm" else "default",
            "text": {"tag": "plain_text", "content": labels[operation]},
            "value": {
                "kind": "a13n.routine.v1",
                "action": operation,
                "routine_id": row.id,
                "token": row.action_token,
            },
        }
        if operation == "delete":
            button["confirm"] = {
                "title": {"tag": "plain_text", "content": labels["confirm_delete"]},
                "text": {"tag": "plain_text", "content": labels["delete_details"]},
            }
        actions.append(button)
    if actions:
        elements.append({"tag": "action", "actions": actions})
    elements.append({"tag": "note", "elements": [{"tag": "plain_text", "content": labels["help"].format(id=row.id)}]})
    return {
        "config": {"wide_screen_mode": True, "update_multi": True, "enable_forward": False},
        "header": {"title": {"tag": "plain_text", "content": title}, "template": "blue"},
        "elements": elements,
    }
