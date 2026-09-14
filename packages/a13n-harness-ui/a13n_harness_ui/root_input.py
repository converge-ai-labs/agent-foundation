"""Detach native semantic input at the App admission boundary."""

from copy import deepcopy
from dataclasses import dataclass, replace

from a13n_harness.environment.models import EnvironmentAction
from a13n_harness.environment.providers import BoundEnvironment
from a13n_harness.input import RunInputValue, normalize_input
from pydantic_ai.messages import TextContent, UserContent

from a13n_harness_ui.configuration.models import InputConfiguration
from a13n_harness_ui.errors import RunCoordinationError
from a13n_harness_ui.thread_files import (
    MAX_ATTACHMENT_BYTES,
    MAX_ATTACHMENTS,
    MAX_INPUT_BYTES,
    AttachmentUpload,
    ThreadFiles,
)


def detach_input(prompt: RunInputValue) -> RunInputValue:
    value = normalize_input(prompt).value
    if (
        value is None
        or (isinstance(value, str) and not value.strip())
        or (isinstance(value, tuple) and all(isinstance(part, str) and not part.strip() for part in value))
    ):
        raise RunCoordinationError("A root message must not be blank.", code="run_input_invalid")
    # BinaryContent and sequence containers are mutable native objects. An
    # accepted operation must not observe the caller's subsequent draft edits.
    return deepcopy(value)


@dataclass(frozen=True, slots=True)
class RootInputFiles:
    """One root Run's captured policy, shared by initial input and human steering."""

    files: ThreadFiles
    thread_id: str
    configuration: InputConfiguration
    view_enabled: bool

    async def prepare(self, prompt: RunInputValue, environment: BoundEnvironment) -> RunInputValue:
        prompt = detach_input(prompt)
        threshold = self.configuration.long_text_threshold_chars
        parts: list[UserContent] = [prompt] if isinstance(prompt, str) else list(prompt)
        candidates = [
            (index, text)
            for index, part in enumerate(parts)
            if (text := _authored_text(part)) is not None and threshold is not None and len(text) > threshold
        ]
        if not candidates:
            return prompt
        mount = next((item for item in environment.snapshot.mounts if item.name == "thread-files"), None)
        required = {EnvironmentAction.FILE_READ_TEXT, EnvironmentAction.FILE_STAT}
        if not self.view_enabled or mount is None or not required <= mount.permission_ceiling.operations:
            return (
                *parts,
                TextContent(
                    "Long text was kept inline because this Run has no enabled file reader for the thread-files mount.",
                    metadata={"harness_ui": {"long_text_skipped": True}},
                ),
            )
        # Resolve through the entered Environment before retaining any input. This
        # also checks that a lazy Thread mount can actually be made ready.
        selection = await environment.resolve_files(".", alias="thread-files")
        if selection.mount_path is None:
            raise RunCoordinationError("The Thread file mount has no aggregate path.", code="run_input_invalid")
        root = selection.mount_path.rstrip("/")
        encoded = [(index, text, text.encode("utf-8")) for index, text in candidates]
        attachments = _attachment_sizes(parts)
        if len(attachments) + len(encoded) > MAX_ATTACHMENTS:
            raise RunCoordinationError(
                "An input supports up to eight attachments, including long-text files.", code="run_input_invalid"
            )
        if any(len(data) > MAX_ATTACHMENT_BYTES for _, _, data in encoded):
            raise RunCoordinationError("Long-text input files must not exceed 10 MiB.", code="run_input_invalid")
        if sum(attachments.values()) + sum(len(data) for _, _, data in encoded) > MAX_INPUT_BYTES:
            raise RunCoordinationError(
                "An input supports up to 20 MiB of attachments, including long-text files.", code="run_input_invalid"
            )
        for index, text, data in encoded:
            attachment = await self.files.stage(
                self.thread_id, AttachmentUpload(f"input-text-{index + 1}.txt", data, "text/plain")
            )
            await self.files.retain(self.thread_id, attachment.attachment_id)
            relative_path = f"attachments/{attachment.attachment_id}/content"
            path = f"{root}/{relative_path}"
            reference = (
                f"The user's submitted text ({len(text)} characters) is saved in a retained input file.\n"
                f"File: {path}\n"
                "Mount: thread-files\n"
                "Read this file with view to understand the user's request before answering. "
                "Read further sections as needed; no part of the original text is included inline."
            )
            original = parts[index]
            metadata = deepcopy(original.metadata or {}) if isinstance(original, TextContent) else {}
            namespace = metadata.get("harness_ui")
            metadata["harness_ui"] = {
                **(namespace if isinstance(namespace, dict) else {}),
                "attachment": attachment.model_dump(),
                "mount": "thread-files",
                "path": relative_path,
                "long_text": True,
                "characters": len(text),
            }
            parts[index] = (
                replace(original, content=reference, metadata=metadata)
                if isinstance(original, TextContent)
                else TextContent(reference, metadata=metadata)
            )
        return tuple(parts)


def _authored_text(part: UserContent) -> str | None:
    if isinstance(part, str):
        return part
    if not isinstance(part, TextContent):
        return None
    metadata = part.metadata or {}
    namespace = metadata.get("harness_ui")
    if metadata.get("display") is False or (
        isinstance(namespace, dict) and ("attachment" in namespace or namespace.get("long_text_skipped"))
    ):
        return None
    return part.content


def _attachment_sizes(parts: list[UserContent]) -> dict[str, int]:
    attachments: dict[str, int] = {}
    for part in parts:
        if not isinstance(part, TextContent):
            continue
        namespace = (part.metadata or {}).get("harness_ui")
        attachment = namespace.get("attachment") if isinstance(namespace, dict) else None
        if isinstance(attachment, dict):
            identifier, size = attachment.get("attachment_id"), attachment.get("size")
            if isinstance(identifier, str) and isinstance(size, int):
                attachments[identifier] = size
    return attachments
