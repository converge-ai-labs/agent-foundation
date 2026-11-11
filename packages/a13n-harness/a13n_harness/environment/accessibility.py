"""Bounded accessibility observations and native-reference contracts.

These values describe provider facts, not authority. Native handles remain owned by
one Session/backend epoch; the bound Environment supplies execution provenance.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, FiniteFloat, model_validator

type AccessibilityBackend = Literal["ax", "uia", "atspi"]
type TextUnit = Literal["character", "format", "word", "line", "paragraph", "page", "document"]
type RangeEndpoint = Literal["start", "end"]
type NativeFrame = Literal["ax_screen", "uia_physical_screen", "atspi_screen", "atspi_window", "atspi_parent"]


def _bounded_text(value: str) -> str:
    if len(value.encode("utf-8")) > 65536 or "\0" in value:
        raise ValueError("Text must contain at most 65536 UTF-8 bytes and no NUL.")
    return value


type AccessibilityText = Annotated[str, Field(max_length=65536), AfterValidator(_bounded_text)]
type NativeHandle = Annotated[str, Field(min_length=1, max_length=128)]


class AccessibilityModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class AccessibilityNodeReference(AccessibilityModel):
    execution_id: str
    observed_generation: str
    backend: AccessibilityBackend
    backend_epoch: NativeHandle
    node_handle: NativeHandle


class AccessibilityRangeReference(AccessibilityModel):
    node: AccessibilityNodeReference
    range_handle: NativeHandle
    unit: Literal["ax_native", "atspi_character", "uia_opaque"]

    @model_validator(mode="after")
    def _native_unit(self) -> Self:
        expected = {"ax": "ax_native", "atspi": "atspi_character", "uia": "uia_opaque"}
        if self.unit != expected[self.node.backend]:
            raise ValueError("Range unit does not match its native backend.")
        return self


class AccessibilityActionReference(AccessibilityModel):
    node: AccessibilityNodeReference
    action_handle: NativeHandle
    native_identifier: str = Field(min_length=1, max_length=1024)


class AccessibilityCursor(AccessibilityModel):
    root: AccessibilityNodeReference
    cursor_handle: NativeHandle


class NativeSpan(AccessibilityModel):
    """Half-open offsets in the named native coordinate system, never UIA offsets."""

    unit: Literal["ax_native", "atspi_character"]
    start: int = Field(ge=0)
    end: int = Field(ge=0)

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.end < self.start:
            raise ValueError("Range end precedes start.")
        return self


class NativePoint(AccessibilityModel):
    frame: NativeFrame
    x: FiniteFloat
    y: FiniteFloat


class NativeBounds(AccessibilityModel):
    frame: NativeFrame
    x: FiniteFloat
    y: FiniteFloat
    width: FiniteFloat = Field(ge=0)
    height: FiniteFloat = Field(ge=0)
    display_id: str | None = None


class AccessibilityFieldError(AccessibilityModel):
    field: str = Field(max_length=128)
    reason: Literal["unsupported", "denied", "stale", "unavailable", "timeout", "protected"]
    native_code: int | None = None


class AccessibilityCapability(AccessibilityModel):
    operation: str = Field(min_length=1, max_length=128)
    supported: Literal["yes", "candidate"]
    source: str = Field(max_length=128)
    action: AccessibilityActionReference | None = None
    label: str | None = Field(default=None, max_length=4096)
    description: str | None = Field(default=None, max_length=8192)


class AccessibilityImageProjection(AccessibilityModel):
    source_observation_id: str
    status: Literal["projected", "unavailable"]
    # Floating-point edges in source-image pixels; clipping and visibility are separate facts.
    left: FiniteFloat | None = None
    top: FiniteFloat | None = None
    right: FiniteFloat | None = None
    bottom: FiniteFloat | None = None

    @model_validator(mode="after")
    def _rectangle(self) -> Self:
        edges = (self.left, self.top, self.right, self.bottom)
        if self.status == "unavailable":
            if any(edge is not None for edge in edges):
                raise ValueError("Unavailable projections cannot carry image geometry.")
        elif self.left is None or self.top is None or self.right is None or self.bottom is None:
            raise ValueError("Projected bounds require all four edges.")
        elif self.right < self.left or self.bottom < self.top:
            raise ValueError("Projection edges are inverted.")
        return self


class AccessibilityNode(AccessibilityModel):
    reference: AccessibilityNodeReference
    native_role: str = Field(max_length=1024)
    role: str | None = Field(default=None, max_length=128)
    name: str | None = Field(default=None, max_length=4096)
    description: str | None = Field(default=None, max_length=8192)
    identifier: str | None = Field(default=None, max_length=4096)
    states: dict[str, bool | str] = Field(default_factory=dict, max_length=128)
    bounds: NativeBounds | None = None
    image_projection: AccessibilityImageProjection | None = None
    parent: AccessibilityNodeReference | None = None
    child_count: int | None = Field(default=None, ge=0)
    children: tuple[AccessibilityNodeReference, ...] = Field(default=(), max_length=2048)
    capabilities: tuple[AccessibilityCapability, ...] = Field(default=(), max_length=128)
    sampled_at: datetime
    field_errors: tuple[AccessibilityFieldError, ...] = Field(default=(), max_length=128)


class AccessibilityLimits(AccessibilityModel):
    depth: int = Field(default=2, ge=0, le=12)
    max_nodes: int = Field(default=256, ge=1, le=2048)
    max_matches: int = Field(default=64, ge=1, le=256)
    max_items: int = Field(default=128, ge=1, le=512)
    max_text_bytes: int = Field(default=16384, ge=1, le=65536)
    max_result_bytes: int = Field(default=262144, ge=1, le=1048576)
    max_native_calls: int = Field(default=1024, ge=1, le=8192)
    timeout_ms: int = Field(default=5000, ge=1, le=15000)


class AccessibilityDescription(AccessibilityModel):
    backend: AccessibilityBackend
    backend_epoch: NativeHandle | None = None
    ready: bool
    reason: Literal["ready", "permission_required", "desktop_unavailable", "bus_unavailable", "unsupported"]
    desktop_identity: str = Field(max_length=1024)
    methods: tuple[str, ...] = Field(default=(), max_length=128)
    frames: tuple[NativeFrame, ...] = ()
    limits: AccessibilityLimits = Field(default_factory=AccessibilityLimits)


class AccessibilityCompleteness(AccessibilityModel):
    complete: bool
    reason: Literal["node_limit", "item_limit", "byte_limit", "call_limit", "deadline", "total_limit"] | None = None
    continuation: AccessibilityCursor | None = None
    consistency: Literal["live"] = "live"

    @model_validator(mode="after")
    def _completion(self) -> Self:
        if self.complete and (self.reason is not None or self.continuation is not None):
            raise ValueError("Complete reads cannot carry truncation or a continuation.")
        if not self.complete and self.reason is None:
            raise ValueError("Incomplete reads require a truncation reason.")
        return self


class AccessibilityTree(AccessibilityModel):
    backend: AccessibilityBackend
    sampled_at: datetime
    nodes: tuple[AccessibilityNode, ...] = Field(max_length=2048)
    completeness: AccessibilityCompleteness


class AccessibilityRootScope(AccessibilityModel):
    kind: Literal["desktop", "applications", "windows", "focused", "application", "at_point"] = "windows"
    process_id: int | None = Field(default=None, gt=0)
    point: NativePoint | None = None

    @model_validator(mode="after")
    def _scope_arguments(self) -> Self:
        if (self.kind == "application") != (self.process_id is not None):
            raise ValueError("Only application scope requires a process ID.")
        if (self.kind == "at_point") != (self.point is not None):
            raise ValueError("Only at_point scope requires a native point.")
        return self


class AccessibilityFind(AccessibilityModel):
    role: str | None = Field(default=None, max_length=128)
    native_role: str | None = Field(default=None, max_length=1024)
    name: str | None = Field(default=None, max_length=4096)
    name_match: Literal["exact", "substring_casefold"] = "exact"
    identifier: str | None = Field(default=None, max_length=4096)
    states: dict[str, bool | str] = Field(default_factory=dict, max_length=128)
    operation: str | None = Field(default=None, max_length=128)


class AccessibilityPropertyRead(AccessibilityModel):
    kind: Literal["properties"] = "properties"
    groups: tuple[Literal["identity", "state", "value", "window"], ...] = Field(min_length=1, max_length=4)


class AccessibilityTextRead(AccessibilityModel):
    kind: Literal["text"] = "text"
    span: NativeSpan | None = None


class AccessibilitySimpleRead(AccessibilityModel):
    kind: Literal["selection", "numeric_value", "scroll", "document", "document_selections"]


class AccessibilityTableRead(AccessibilityModel):
    kind: Literal["table"] = "table"
    row: int = Field(default=0, ge=0)
    column: int = Field(default=0, ge=0)
    rows: int = Field(default=1, ge=1, le=512)
    columns: int = Field(default=1, ge=1, le=512)
    include_headers: bool = True
    include_selection: bool = False

    @model_validator(mode="after")
    def _cell_bound(self) -> Self:
        if self.rows * self.columns > 512:
            raise ValueError("Table request exceeds 512 cells.")
        return self


class AccessibilityRelationsRead(AccessibilityModel):
    kind: Literal["relations"] = "relations"
    relations: tuple[str, ...] = Field(default=(), max_length=64)


class AccessibilityNativeRead(AccessibilityModel):
    kind: Literal["native_details"] = "native_details"
    group: Literal[
        "ax_attributes",
        "ax_parameterized_attributes",
        "uia_patterns",
        "uia_annotation",
        "uia_styles",
        "uia_drag",
        "uia_drop_target",
        "uia_legacy",
        "uia_transform",
        "uia_views",
        "uia_spreadsheet_item",
        "atspi_interfaces",
        "atspi_image",
        "atspi_hypertext",
        "atspi_component",
        "atspi_actions",
    ]


class AccessibilityItemRead(AccessibilityModel):
    kind: Literal["item"] = "item"
    source: Literal["uia_item_container", "uia_spreadsheet"]
    name: str = Field(max_length=4096)
    after: AccessibilityNodeReference | None = None


class AccessibilityNavigateRead(AccessibilityModel):
    kind: Literal["navigate"] = "navigate"
    direction: Literal["parent", "next_sibling", "previous_sibling", "first_child", "last_child"]


class AccessibilityIdleRead(AccessibilityModel):
    kind: Literal["wait_for_input_idle"] = "wait_for_input_idle"
    timeout_ms: int = Field(default=1000, ge=0, le=15000)


type AccessibilityRead = Annotated[
    AccessibilityPropertyRead
    | AccessibilityTextRead
    | AccessibilitySimpleRead
    | AccessibilityTableRead
    | AccessibilityRelationsRead
    | AccessibilityNativeRead
    | AccessibilityItemRead
    | AccessibilityNavigateRead
    | AccessibilityIdleRead,
    Field(discriminator="kind"),
]


class AccessibilityScalar(AccessibilityModel):
    kind: Literal["scalar"] = "scalar"
    value: bool | int | FiniteFloat | str


class AccessibilitySpecialValue(AccessibilityModel):
    kind: Literal["mixed", "unsupported", "protected", "unavailable"]


class AccessibilityNodesValue(AccessibilityModel):
    kind: Literal["nodes"] = "nodes"
    values: tuple[AccessibilityNodeReference, ...] = Field(max_length=512)


class AccessibilityRangesValue(AccessibilityModel):
    kind: Literal["ranges"] = "ranges"
    values: tuple[AccessibilityRangeReference, ...] = Field(max_length=256)


class AccessibilityScalarsValue(AccessibilityModel):
    kind: Literal["scalars"] = "scalars"
    values: tuple[bool | int | FiniteFloat | str, ...] = Field(max_length=512)


class AccessibilityBoundsValue(AccessibilityModel):
    kind: Literal["bounds"] = "bounds"
    values: tuple[NativeBounds, ...] = Field(max_length=512)


type AccessibilityValue = Annotated[
    AccessibilityScalar
    | AccessibilitySpecialValue
    | AccessibilityNodesValue
    | AccessibilityRangesValue
    | AccessibilityScalarsValue
    | AccessibilityBoundsValue,
    Field(discriminator="kind"),
]


class AccessibilityProperty(AccessibilityModel):
    name: str = Field(min_length=1, max_length=128)
    source: str = Field(max_length=128)
    value: AccessibilityValue


class AccessibilityTextContent(AccessibilityModel):
    status: Literal["text", "empty", "unsupported", "protected", "unavailable"]
    source: str = Field(max_length=128)
    text: AccessibilityText | None = None
    span: NativeSpan | None = None

    @model_validator(mode="after")
    def _text_status(self) -> Self:
        if self.status == "text" and not self.text:
            raise ValueError("Text status requires nonempty text.")
        if self.status == "empty" and self.text != "":
            raise ValueError("Empty status requires an empty string.")
        if self.status not in {"text", "empty"} and (self.text is not None or self.span is not None):
            raise ValueError("Unavailable or protected content cannot carry text or offsets.")
        return self


class AtspiDocumentSelection(AccessibilityModel):
    """A native Document selection whose endpoints can belong to different text objects."""

    start: AccessibilityNodeReference
    start_offset: int = Field(ge=0)
    end: AccessibilityNodeReference
    end_offset: int = Field(ge=0)
    start_is_active: bool


class AccessibilityAttributeMetadata(AccessibilityModel):
    name: str = Field(min_length=1, max_length=1024)
    parameterized: bool
    writable: bool | None = None


class AccessibilityContent(AccessibilityModel):
    node: AccessibilityNodeReference
    sampled_at: datetime
    completeness: AccessibilityCompleteness
    text: AccessibilityTextContent | None = None
    properties: tuple[AccessibilityProperty, ...] = Field(default=(), max_length=512)
    nodes: tuple[AccessibilityNode, ...] = Field(default=(), max_length=512)
    ranges: tuple[AccessibilityRangeReference, ...] = Field(default=(), max_length=256)
    field_errors: tuple[AccessibilityFieldError, ...] = Field(default=(), max_length=128)
    document_selections: tuple[AtspiDocumentSelection, ...] = Field(default=(), max_length=256)
    attributes: tuple[AccessibilityAttributeMetadata, ...] = Field(default=(), max_length=512)


class AccessibilityRangeAcquire(AccessibilityModel):
    kind: Literal["document", "selection", "visible", "caret", "active_composition", "conversion_target"]


class AccessibilityRangeSpan(AccessibilityModel):
    kind: Literal["span"] = "span"
    span: NativeSpan


class AccessibilityRangePoint(AccessibilityModel):
    kind: Literal["point"] = "point"
    point: NativePoint


class AccessibilityRangeChild(AccessibilityModel):
    kind: Literal["child"] = "child"
    child: AccessibilityNodeReference


class AccessibilityRangeFind(AccessibilityModel):
    kind: Literal["find_text"] = "find_text"
    text: AccessibilityText
    backward: bool = False
    ignore_case: bool = False


class AccessibilityRangeFindAttribute(AccessibilityModel):
    kind: Literal["find_attribute"] = "find_attribute"
    attribute_id: int = Field(ge=0)
    value: AccessibilityScalar
    backward: bool = False


class AccessibilityRangeMove(AccessibilityModel):
    kind: Literal["move", "move_endpoint", "expand"]
    unit: TextUnit
    count: int = Field(default=0, ge=-10000, le=10000)
    endpoint: RangeEndpoint | None = None

    @model_validator(mode="after")
    def _movement(self) -> Self:
        if (self.kind == "move_endpoint") != (self.endpoint is not None):
            raise ValueError("Only endpoint movement takes an endpoint.")
        if self.kind == "expand" and self.count != 0:
            raise ValueError("Expand does not take a movement count.")
        return self


class AccessibilityRangeCompose(AccessibilityModel):
    kind: Literal["endpoint_by_range"] = "endpoint_by_range"
    endpoint: RangeEndpoint
    other: AccessibilityRangeReference
    other_endpoint: RangeEndpoint


class AccessibilityRangeIndex(AccessibilityModel):
    kind: Literal["index", "line", "line_for_index"]
    index: int = Field(ge=0)


type AccessibilityRangeQuery = Annotated[
    AccessibilityRangeAcquire
    | AccessibilityRangeSpan
    | AccessibilityRangePoint
    | AccessibilityRangeChild
    | AccessibilityRangeFind
    | AccessibilityRangeFindAttribute
    | AccessibilityRangeMove
    | AccessibilityRangeCompose
    | AccessibilityRangeIndex,
    Field(discriminator="kind"),
]


class AccessibilityRangeRead(AccessibilityModel):
    kind: Literal["text", "attributes", "bounds", "enclosing_element", "children"]
    attribute_ids: tuple[int, ...] = Field(default=(), max_length=128)


class AccessibilityRangeCompare(AccessibilityModel):
    kind: Literal["compare"] = "compare"
    other: AccessibilityRangeReference
    endpoint: RangeEndpoint | None = None
    other_endpoint: RangeEndpoint | None = None

    @model_validator(mode="after")
    def _endpoints(self) -> Self:
        if (self.endpoint is None) != (self.other_endpoint is None):
            raise ValueError("Endpoint comparison requires both endpoints.")
        return self


type AccessibilityRangeReadRequest = Annotated[
    AccessibilityRangeRead | AccessibilityRangeCompare, Field(discriminator="kind")
]


class AccessibilityAttributeRun(AccessibilityModel):
    range: AccessibilityRangeReference
    properties: tuple[AccessibilityProperty, ...] = Field(max_length=128)


class AccessibilityRangeResult(AccessibilityModel):
    node: AccessibilityNodeReference
    sampled_at: datetime
    completeness: AccessibilityCompleteness
    ranges: tuple[AccessibilityRangeReference, ...] = Field(default=(), max_length=256)
    actual_movement: int | None = None
    native_index: int | None = None
    comparison: int | bool | None = None
    text: AccessibilityTextContent | None = None
    bounds: tuple[NativeBounds, ...] = Field(default=(), max_length=512)
    properties: tuple[AccessibilityProperty, ...] = Field(default=(), max_length=128)
    attribute_runs: tuple[AccessibilityAttributeRun, ...] = Field(default=(), max_length=512)
    nodes: tuple[AccessibilityNode, ...] = Field(default=(), max_length=512)
    field_errors: tuple[AccessibilityFieldError, ...] = Field(default=(), max_length=128)
