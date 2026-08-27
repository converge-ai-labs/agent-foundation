"""Structured user-question tool over Pydantic AI native deferral."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets import ExternalToolset

from a13n_harness.context import AgentContext

ASK_USER_QUESTION_KIND = "ask_user_question"
ASK_USER_QUESTION_TOOL_NAME = "ask_user_question"


class UserQuestionOption(BaseModel):
    """One selectable answer shown by the Host."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    label: str = Field(min_length=1, max_length=256)
    description: str = Field(min_length=1, max_length=4_096)


class UserQuestion(BaseModel):
    """One bounded structured question."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        str_strip_whitespace=True,
        populate_by_name=True,
    )

    question: str = Field(min_length=1, max_length=8_192)
    header: str = Field(min_length=1, max_length=12)
    options: tuple[UserQuestionOption, ...] = Field(min_length=2, max_length=4)
    multi_select: bool = Field(default=False, alias="multiSelect")

    @model_validator(mode="after")
    def _validate_options(self) -> UserQuestion:
        labels = [option.label for option in self.options]
        if len(set(labels)) != len(labels):
            raise ValueError("question option labels must be unique")
        return self


class AskUserQuestionRequest(BaseModel):
    """Exact deferred request schema presented by the Host."""

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    questions: tuple[UserQuestion, ...] = Field(min_length=1, max_length=4)

    @model_validator(mode="after")
    def _validate_questions(self) -> AskUserQuestionRequest:
        texts = [question.question for question in self.questions]
        if len(set(texts)) != len(texts):
            raise ValueError("question texts must be unique")
        return self


class UserQuestionAnswers(BaseModel):
    """Untrusted Host result correlated to one pending question call."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    answers: dict[str, str | tuple[str, ...]] = Field(default_factory=dict)
    response: str | None = Field(default=None, min_length=1, max_length=64 * 1024)

    @model_validator(mode="after")
    def _validate_values(self) -> UserQuestionAnswers:
        if len(self.answers) > 4:
            raise ValueError("too many question answers")
        for question, answer in self.answers.items():
            if not question.strip() or len(question) > 8_192:
                raise ValueError("answer question key is invalid")
            values = (answer,) if isinstance(answer, str) else answer
            if not values or len(values) > 4 or any(not value.strip() or len(value) > 8_192 for value in values):
                raise ValueError("question answer is invalid")
        return self


class UserInteractionToolset:
    """Pure model-facing schema expressed as one native external Toolset."""

    def get_toolset(self) -> ExternalToolset[AgentContext]:
        definition = ToolDefinition(
            name=ASK_USER_QUESTION_TOOL_NAME,
            description=(
                "Ask the user one to four clarifying questions when their answers materially affect the result. "
                "Each question provides two to four options and may allow multiple selections."
            ),
            parameters_json_schema=AskUserQuestionRequest.model_json_schema(by_alias=True),
            metadata={"kind": ASK_USER_QUESTION_KIND},
        )
        return ExternalToolset([definition], id="a13n-user-interaction-tools")


def validate_user_question_result(arguments: object, value: object) -> dict[str, object]:
    """Validate and normalize one answer against its exact pending question request."""
    request = AskUserQuestionRequest.model_validate(arguments)
    answers = UserQuestionAnswers.model_validate(value)
    expected = {question.question: question for question in request.questions}
    actual = set(answers.answers)
    if not actual <= set(expected):
        raise ValueError("answer contains an unknown question")
    if set(expected) - actual and answers.response is None:
        raise ValueError("answer must cover every question or include a general response")
    for text, answer in answers.answers.items():
        question = expected[text]
        values = (answer,) if isinstance(answer, str) else answer
        labels = {option.label for option in question.options}
        selected = tuple(value for value in values if value in labels)
        if selected and len(selected) != len(values):
            raise ValueError("answer cannot mix option labels and free text")
        if selected and not question.multi_select and len(selected) != 1:
            raise ValueError("single-select question requires exactly one option")
    return answers.model_dump(mode="json", exclude_none=True)


__all__ = [
    "ASK_USER_QUESTION_KIND",
    "ASK_USER_QUESTION_TOOL_NAME",
    "AskUserQuestionRequest",
    "UserInteractionToolset",
    "UserQuestion",
    "UserQuestionAnswers",
    "UserQuestionOption",
    "validate_user_question_result",
]
