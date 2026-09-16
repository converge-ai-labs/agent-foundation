from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.creation_metadata import CreationMetadata
    from ..models.remove_operation import RemoveOperation
    from ..models.replace_text_operation import ReplaceTextOperation
    from ..models.set_operation import SetOperation


T = TypeVar("T", bound="UpdateConfigurationDraftRequest")


@_attrs_define(repr=False)
class UpdateConfigurationDraftRequest:
    """
    Attributes:
        expected_version (int):
        creation_metadata (CreationMetadata | None | Unset):
        expected_digest (None | str | Unset):
        operations (list[RemoveOperation | ReplaceTextOperation | SetOperation] | Unset):
    """

    expected_version: int
    creation_metadata: CreationMetadata | Unset | None = UNSET
    expected_digest: str | Unset | None = UNSET
    operations: list[RemoveOperation | ReplaceTextOperation | SetOperation] | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.creation_metadata import CreationMetadata
        from ..models.remove_operation import RemoveOperation
        from ..models.set_operation import SetOperation

        expected_version = self.expected_version

        creation_metadata: dict[str, Any] | Unset | None
        if isinstance(self.creation_metadata, Unset):
            creation_metadata = UNSET
        elif isinstance(self.creation_metadata, CreationMetadata):
            creation_metadata = self.creation_metadata.to_dict()
        else:
            creation_metadata = self.creation_metadata

        expected_digest: str | Unset | None
        if isinstance(self.expected_digest, Unset):
            expected_digest = UNSET
        else:
            expected_digest = self.expected_digest

        operations: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.operations, Unset):
            operations = []
            for operations_item_data in self.operations:
                operations_item: dict[str, Any]
                if isinstance(operations_item_data, SetOperation):
                    operations_item = operations_item_data.to_dict()
                elif isinstance(operations_item_data, RemoveOperation):
                    operations_item = operations_item_data.to_dict()
                else:
                    operations_item = operations_item_data.to_dict()

                operations.append(operations_item)

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "expected_version": expected_version,
            }
        )
        if creation_metadata is not UNSET:
            field_dict["creation_metadata"] = creation_metadata
        if expected_digest is not UNSET:
            field_dict["expected_digest"] = expected_digest
        if operations is not UNSET:
            field_dict["operations"] = operations

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.creation_metadata import CreationMetadata
        from ..models.remove_operation import RemoveOperation
        from ..models.replace_text_operation import ReplaceTextOperation
        from ..models.set_operation import SetOperation

        d = dict(src_dict)
        expected_version = d.pop("expected_version")

        def _parse_creation_metadata(data: object) -> CreationMetadata | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                creation_metadata_type_0 = CreationMetadata.from_dict(data)

                return creation_metadata_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(CreationMetadata | Unset | None, data)

        creation_metadata = _parse_creation_metadata(d.pop("creation_metadata", UNSET))

        def _parse_expected_digest(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        expected_digest = _parse_expected_digest(d.pop("expected_digest", UNSET))

        _operations = d.pop("operations", UNSET)
        operations: list[RemoveOperation | ReplaceTextOperation | SetOperation] | Unset = UNSET
        if _operations is not UNSET:
            operations = []
            for operations_item_data in _operations:

                def _parse_operations_item(data: object) -> RemoveOperation | ReplaceTextOperation | SetOperation:
                    try:
                        if not isinstance(data, dict):
                            raise TypeError()
                        operations_item_type_0 = SetOperation.from_dict(data)

                        return operations_item_type_0
                    except (TypeError, ValueError, AttributeError, KeyError):
                        pass
                    try:
                        if not isinstance(data, dict):
                            raise TypeError()
                        operations_item_type_1 = RemoveOperation.from_dict(data)

                        return operations_item_type_1
                    except (TypeError, ValueError, AttributeError, KeyError):
                        pass
                    if not isinstance(data, dict):
                        raise TypeError()
                    operations_item_type_2 = ReplaceTextOperation.from_dict(data)

                    return operations_item_type_2

                operations_item = _parse_operations_item(operations_item_data)

                operations.append(operations_item)

        update_configuration_draft_request = cls(
            expected_version=expected_version,
            creation_metadata=creation_metadata,
            expected_digest=expected_digest,
            operations=operations,
        )

        return update_configuration_draft_request
