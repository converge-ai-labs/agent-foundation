"""Retain explicitly exposed computer screenshots without changing model content."""

from __future__ import annotations

from a13n_harness import HarnessEvent
from ag_ui.core import CustomEvent
from pydantic import ValidationError
from pydantic_ai import BinaryContent
from pydantic_ai.messages import FunctionToolResultEvent, ToolReturnPart

from a13n_harness_ui.surfaces import ToolImageView
from a13n_harness_ui.thread_files import AttachmentUpload, ThreadFiles

_METADATA_KEY = "a13n.harness-ui.tool_images"
_ERROR_KEY = "a13n.harness-ui.tool_image_unavailable"


class ToolImageCollector:
    def __init__(self, *, run_id: str, thread_id: str, files: ThreadFiles | None) -> None:
        self._run_id = run_id
        self._thread_id = thread_id
        self._files = files

    async def observe(self, item: object) -> tuple[CustomEvent, ...]:
        if not isinstance(item, HarnessEvent) or item.run_id != self._run_id:
            return ()
        event = item.event
        if not isinstance(event, FunctionToolResultEvent) or not isinstance(event.part, ToolReturnPart):
            return ()
        part = event.part
        if not isinstance(part.metadata, dict) or part.metadata.get("a13n.computer.screenshot") is not True:
            return ()
        images: list[ToolImageView] = []
        unavailable = False
        content = event.content
        if content is not None and not isinstance(content, str):
            for media in content:
                if not isinstance(media, BinaryContent) or media.media_type not in {"image/jpeg", "image/png"}:
                    continue
                if self._files is None:
                    unavailable = True
                    continue
                try:
                    suffix = "jpg" if media.media_type == "image/jpeg" else "png"
                    attachment = await self._files.stage(
                        self._thread_id,
                        AttachmentUpload(
                            name=f"computer-{part.tool_call_id}.{suffix}",
                            data=media.data,
                            media_type=media.media_type,
                        ),
                    )
                    images.append(ToolImageView(thread_id=self._thread_id, attachment=attachment))
                except (OSError, ValueError):
                    # Retention failure must not fail or replay an otherwise successful tool.
                    unavailable = True
        projected = [image.model_dump(mode="json") for image in images]
        part.metadata = {**part.metadata, _METADATA_KEY: projected, _ERROR_KEY: unavailable}
        return (
            CustomEvent(
                name="a13n.harness-ui.tool_images",
                value={
                    "run_id": self._run_id,
                    "event": {"tool_call_id": part.tool_call_id, "images": projected, "unavailable": unavailable},
                },
            ),
        )


def tool_images(part: ToolReturnPart) -> tuple[ToolImageView, ...]:
    metadata = part.metadata
    if not isinstance(metadata, dict) or not isinstance(metadata.get(_METADATA_KEY), list):
        return ()
    try:
        return tuple(ToolImageView.model_validate(image) for image in metadata[_METADATA_KEY])
    except (ValidationError, TypeError):
        return ()


def tool_image_unavailable(part: ToolReturnPart) -> bool:
    return isinstance(part.metadata, dict) and part.metadata.get(_ERROR_KEY) is True
