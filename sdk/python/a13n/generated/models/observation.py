from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.observation_status_type_0 import ObservationStatusType0

if TYPE_CHECKING:
    from ..models.content import Content
    from ..models.instrumentation_scope import InstrumentationScope
    from ..models.model_identity import ModelIdentity
    from ..models.observation_attributes_type_0 import ObservationAttributesType0
    from ..models.observation_event import ObservationEvent
    from ..models.observation_link import ObservationLink
    from ..models.observation_resource_attributes_type_0 import ObservationResourceAttributesType0
    from ..models.observation_usage_type_0 import ObservationUsageType0


T = TypeVar("T", bound="Observation")


@_attrs_define(repr=False)
class Observation:
    """
    Attributes:
        attributes (None | ObservationAttributesType0):
        cost_usd (None | str):
        ended_at (datetime.datetime | None):
        events (list[ObservationEvent] | None):
        id (str):
        input_ (Content | None):
        level (None | str):
        links (list[ObservationLink] | None):
        model (ModelIdentity | None):
        name (str):
        output (Content | None):
        parent_id (None | str):
        resource_attributes (None | ObservationResourceAttributesType0):
        scope (InstrumentationScope | None):
        started_at (datetime.datetime):
        status (None | ObservationStatusType0):
        status_message (None | str):
        type_ (str):
        usage (None | ObservationUsageType0):
    """

    attributes: ObservationAttributesType0 | None
    cost_usd: str | None
    ended_at: datetime.datetime | None
    events: list[ObservationEvent] | None
    id: str
    input_: Content | None
    level: str | None
    links: list[ObservationLink] | None
    model: ModelIdentity | None
    name: str
    output: Content | None
    parent_id: str | None
    resource_attributes: ObservationResourceAttributesType0 | None
    scope: InstrumentationScope | None
    started_at: datetime.datetime
    status: ObservationStatusType0 | None
    status_message: str | None
    type_: str
    usage: ObservationUsageType0 | None

    def to_dict(self) -> dict[str, Any]:
        from ..models.content import Content
        from ..models.instrumentation_scope import InstrumentationScope
        from ..models.model_identity import ModelIdentity
        from ..models.observation_attributes_type_0 import ObservationAttributesType0
        from ..models.observation_resource_attributes_type_0 import ObservationResourceAttributesType0
        from ..models.observation_usage_type_0 import ObservationUsageType0

        attributes: dict[str, Any] | None
        if isinstance(self.attributes, ObservationAttributesType0):
            attributes = self.attributes.to_dict()
        else:
            attributes = self.attributes

        cost_usd: str | None
        cost_usd = self.cost_usd

        ended_at: str | None
        if isinstance(self.ended_at, datetime.datetime):
            ended_at = self.ended_at.isoformat()
        else:
            ended_at = self.ended_at

        events: list[dict[str, Any]] | None
        if isinstance(self.events, list):
            events = []
            for events_type_0_item_data in self.events:
                events_type_0_item = events_type_0_item_data.to_dict()
                events.append(events_type_0_item)

        else:
            events = self.events

        id = self.id

        input_: dict[str, Any] | None
        if isinstance(self.input_, Content):
            input_ = self.input_.to_dict()
        else:
            input_ = self.input_

        level: str | None
        level = self.level

        links: list[dict[str, Any]] | None
        if isinstance(self.links, list):
            links = []
            for links_type_0_item_data in self.links:
                links_type_0_item = links_type_0_item_data.to_dict()
                links.append(links_type_0_item)

        else:
            links = self.links

        model: dict[str, Any] | None
        if isinstance(self.model, ModelIdentity):
            model = self.model.to_dict()
        else:
            model = self.model

        name = self.name

        output: dict[str, Any] | None
        if isinstance(self.output, Content):
            output = self.output.to_dict()
        else:
            output = self.output

        parent_id: str | None
        parent_id = self.parent_id

        resource_attributes: dict[str, Any] | None
        if isinstance(self.resource_attributes, ObservationResourceAttributesType0):
            resource_attributes = self.resource_attributes.to_dict()
        else:
            resource_attributes = self.resource_attributes

        scope: dict[str, Any] | None
        if isinstance(self.scope, InstrumentationScope):
            scope = self.scope.to_dict()
        else:
            scope = self.scope

        started_at = self.started_at.isoformat()

        status: str | None
        if isinstance(self.status, ObservationStatusType0):
            status = self.status.value
        else:
            status = self.status

        status_message: str | None
        status_message = self.status_message

        type_ = self.type_

        usage: dict[str, Any] | None
        if isinstance(self.usage, ObservationUsageType0):
            usage = self.usage.to_dict()
        else:
            usage = self.usage

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "attributes": attributes,
                "cost_usd": cost_usd,
                "ended_at": ended_at,
                "events": events,
                "id": id,
                "input": input_,
                "level": level,
                "links": links,
                "model": model,
                "name": name,
                "output": output,
                "parent_id": parent_id,
                "resource_attributes": resource_attributes,
                "scope": scope,
                "started_at": started_at,
                "status": status,
                "status_message": status_message,
                "type": type_,
                "usage": usage,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.content import Content
        from ..models.instrumentation_scope import InstrumentationScope
        from ..models.model_identity import ModelIdentity
        from ..models.observation_attributes_type_0 import ObservationAttributesType0
        from ..models.observation_event import ObservationEvent
        from ..models.observation_link import ObservationLink
        from ..models.observation_resource_attributes_type_0 import ObservationResourceAttributesType0
        from ..models.observation_usage_type_0 import ObservationUsageType0

        d = dict(src_dict)

        def _parse_attributes(data: object) -> ObservationAttributesType0 | None:
            if data is None:
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                attributes_type_0 = ObservationAttributesType0.from_dict(data)

                return attributes_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(ObservationAttributesType0 | None, data)

        attributes = _parse_attributes(d.pop("attributes"))

        def _parse_cost_usd(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        cost_usd = _parse_cost_usd(d.pop("cost_usd"))

        def _parse_ended_at(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                ended_at_type_0 = datetime.datetime.fromisoformat(data)

                return ended_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None, data)

        ended_at = _parse_ended_at(d.pop("ended_at"))

        def _parse_events(data: object) -> list[ObservationEvent] | None:
            if data is None:
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                events_type_0 = []
                _events_type_0 = data
                for events_type_0_item_data in _events_type_0:
                    events_type_0_item = ObservationEvent.from_dict(events_type_0_item_data)

                    events_type_0.append(events_type_0_item)

                return events_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(list[ObservationEvent] | None, data)

        events = _parse_events(d.pop("events"))

        id = d.pop("id")

        def _parse_input_(data: object) -> Content | None:
            if data is None:
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                input_type_0 = Content.from_dict(data)

                return input_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(Content | None, data)

        input_ = _parse_input_(d.pop("input"))

        def _parse_level(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        level = _parse_level(d.pop("level"))

        def _parse_links(data: object) -> list[ObservationLink] | None:
            if data is None:
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                links_type_0 = []
                _links_type_0 = data
                for links_type_0_item_data in _links_type_0:
                    links_type_0_item = ObservationLink.from_dict(links_type_0_item_data)

                    links_type_0.append(links_type_0_item)

                return links_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(list[ObservationLink] | None, data)

        links = _parse_links(d.pop("links"))

        def _parse_model(data: object) -> ModelIdentity | None:
            if data is None:
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                model_type_0 = ModelIdentity.from_dict(data)

                return model_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(ModelIdentity | None, data)

        model = _parse_model(d.pop("model"))

        name = d.pop("name")

        def _parse_output(data: object) -> Content | None:
            if data is None:
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                output_type_0 = Content.from_dict(data)

                return output_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(Content | None, data)

        output = _parse_output(d.pop("output"))

        def _parse_parent_id(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        parent_id = _parse_parent_id(d.pop("parent_id"))

        def _parse_resource_attributes(data: object) -> ObservationResourceAttributesType0 | None:
            if data is None:
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                resource_attributes_type_0 = ObservationResourceAttributesType0.from_dict(data)

                return resource_attributes_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(ObservationResourceAttributesType0 | None, data)

        resource_attributes = _parse_resource_attributes(d.pop("resource_attributes"))

        def _parse_scope(data: object) -> InstrumentationScope | None:
            if data is None:
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                scope_type_0 = InstrumentationScope.from_dict(data)

                return scope_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(InstrumentationScope | None, data)

        scope = _parse_scope(d.pop("scope"))

        started_at = datetime.datetime.fromisoformat(d.pop("started_at"))

        def _parse_status(data: object) -> ObservationStatusType0 | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                status_type_0 = ObservationStatusType0(data)

                return status_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(ObservationStatusType0 | None, data)

        status = _parse_status(d.pop("status"))

        def _parse_status_message(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        status_message = _parse_status_message(d.pop("status_message"))

        type_ = d.pop("type")

        def _parse_usage(data: object) -> ObservationUsageType0 | None:
            if data is None:
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                usage_type_0 = ObservationUsageType0.from_dict(data)

                return usage_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(ObservationUsageType0 | None, data)

        usage = _parse_usage(d.pop("usage"))

        observation = cls(
            attributes=attributes,
            cost_usd=cost_usd,
            ended_at=ended_at,
            events=events,
            id=id,
            input_=input_,
            level=level,
            links=links,
            model=model,
            name=name,
            output=output,
            parent_id=parent_id,
            resource_attributes=resource_attributes,
            scope=scope,
            started_at=started_at,
            status=status,
            status_message=status_message,
            type_=type_,
            usage=usage,
        )

        return observation
