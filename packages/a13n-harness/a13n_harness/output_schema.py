"""Build the shared model and plugin validator for declarative output."""

from copy import deepcopy
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from pydantic import GetCoreSchemaHandler
from pydantic_ai.output import StructuredDict
from pydantic_core import core_schema
from referencing import Registry
from referencing.exceptions import Unresolvable


def structured_output_type(
    schema: dict[str, Any], *, name: str | None = None, description: str | None = None
) -> type[dict[str, Any]]:
    """Preserve the provider schema while enforcing it on completed values."""
    schema = deepcopy(schema)
    Draft202012Validator.check_schema(schema)
    # Output validation must never fetch schema references from the network.
    validator = Draft202012Validator(schema, registry=Registry())

    def validate(value: dict[str, Any]) -> dict[str, Any]:
        try:
            validator.validate(value)
        except ValidationError as error:
            path = "/".join(str(part) for part in error.absolute_path) or "<root>"
            raise ValueError(f"Output violates {error.validator} at {path}") from error
        except Unresolvable as error:
            raise ValueError("Output schema contains an unresolved reference") from error
        return value

    # Preserve native model-like metadata and the output tool's object argument shape.
    base = StructuredDict(schema, name=name, description=description)

    class ValidatedOutput(base):
        @classmethod
        def __get_pydantic_core_schema__(
            cls, source_type: Any, handler: GetCoreSchemaHandler
        ) -> core_schema.CoreSchema:
            return core_schema.no_info_after_validator_function(validate, handler.generate_schema(base))

    return ValidatedOutput
