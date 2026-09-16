"""Applied-edit evidence retained with the existing tool-return history."""

from __future__ import annotations

from a13n_harness import HarnessEvent
from a13n_harness.toolsets.events import FileEditAppliedEvent
from pydantic import ValidationError
from pydantic_ai.messages import FunctionToolResultEvent, ToolReturnPart

from a13n_harness_ui.surfaces import AppliedEditView

_METADATA_KEY = "a13n.harness-ui.applied_edit"


class ToolEvidenceCollector:
    """Annotate actual result parts before continuation capture, never model-facing content."""

    def __init__(self, *, run_id: str) -> None:
        self.run_id = run_id
        self._pending: dict[str, AppliedEditView] = {}

    def observe(self, item: object) -> None:
        if not isinstance(item, HarnessEvent) or item.run_id != self.run_id:
            return
        event = item.event
        if isinstance(event, FileEditAppliedEvent) and event.tool_call_id:
            self._pending[event.tool_call_id] = AppliedEditView(
                file_path=event.file_path,
                before=event.before,
                after=event.after,
            )
        elif isinstance(event, FunctionToolResultEvent) and isinstance(event.part, ToolReturnPart):
            part = event.part
            edit = self._pending.pop(part.tool_call_id, None)
            if edit is not None and (part.metadata is None or isinstance(part.metadata, dict)):
                part.metadata = {**(part.metadata or {}), _METADATA_KEY: edit.model_dump(mode="json")}


def applied_edit(part: ToolReturnPart) -> AppliedEditView | None:
    """Project only the recognized application metadata; older history needs no migration."""
    if not isinstance(part.metadata, dict) or _METADATA_KEY not in part.metadata:
        return None
    try:
        return AppliedEditView.model_validate(part.metadata[_METADATA_KEY])
    except ValidationError:
        return None
