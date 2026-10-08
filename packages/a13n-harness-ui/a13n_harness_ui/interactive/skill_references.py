"""Style editable Skill text without inserting characters or atomic tokens."""

from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.layout.processors import Processor, Transformation, TransformationInput

from a13n_harness_ui.skill_input import skill_tokens
from a13n_harness_ui.thread_files import ComposerInput

from .commands import CommandRegistry
from .inline_attachments import InlineAttachments


def preview_skill_ranges(prompt: ComposerInput, registry: CommandRegistry) -> tuple[tuple[int, int], ...]:
    ranges = []
    offset = 0
    for part in prompt.parts:
        if isinstance(part, str):
            ranges.extend(
                (offset + start, offset + end) for name, start, end in skill_tokens(part) if name in registry.skills
            )
        offset += len(ComposerInput((part,)).display_text)
    return tuple(ranges)


class SkillProcessor(Processor):
    def __init__(self, registry: CommandRegistry, attachments: InlineAttachments) -> None:
        self.registry = registry
        self.attachments = attachments

    def apply_transformation(self, transformation_input: TransformationInput) -> Transformation:
        text = transformation_input.document.lines[transformation_input.lineno]
        marked: set[int] = set()
        start = 0
        boundaries = [index for index, char in enumerate(text) if char in self.attachments.values] + [len(text)]
        for end in boundaries:
            for name, a, b in skill_tokens(text[start:end]):
                if name in self.registry.skills:
                    marked.update(range(start + a, start + b))
            start = end + 1
        fragments: StyleAndTextTuples = []
        offset = 0
        for fragment in transformation_input.fragments:
            style, value = fragment[:2]
            for char in value:
                fragments.append((style + (" class:input-area.skill" if offset in marked else ""), char))
                offset += 1
        return Transformation(fragments)
