"""WebUI-only trusted Connector discovery with explicit collision diagnostics."""

from importlib.metadata import EntryPoint, entry_points

from .forms import validate_form
from .models import CatalogItem, Declaration
from .sdk import Definition


def _declaration(definition: Definition) -> Declaration:
    declaration = definition.declaration()
    validate_form(declaration.configuration_schema)
    for event in declaration.events:
        model = definition.condition_models.get(event.key)
        expected = model.model_json_schema() if model is not None else None
        if event.conditions_schema != expected:
            raise ValueError("Event conditions must match the declared validator")
        if expected is not None:
            validate_form(expected)
    return declaration


class Catalog:
    def __init__(
        self, definitions: tuple[Definition, ...] = (), *, installed: tuple[EntryPoint, ...] | None = None
    ) -> None:
        from .github import GitHub

        candidates: dict[str, list[tuple[Definition | None, CatalogItem]]] = {}
        for definition in (GitHub(), *definitions):
            declaration = definition.declaration()
            implementation = f"{type(definition).__module__}:{type(definition).__qualname__}"
            try:
                declaration = _declaration(definition)
                pair = (
                    definition,
                    CatalogItem(key=declaration.key, declaration=declaration, implementation=implementation),
                )
            except Exception as exc:
                pair = (
                    None,
                    CatalogItem(
                        key=declaration.key,
                        available=False,
                        diagnostic=f"Plugin declaration failed ({type(exc).__name__}).",
                        implementation=implementation,
                    ),
                )
            candidates.setdefault(declaration.key, []).append(pair)
        for entry in installed if installed is not None else entry_points(group="a13n_harness_ui.connectors"):
            distribution = entry.dist
            provenance = f"{entry.value}@{distribution.name if distribution else ''}=={distribution.version if distribution else ''}"
            try:
                definition = entry.load()()
                if not isinstance(definition, Definition):
                    raise TypeError("Connector entry point must construct a Definition")
                declaration = _declaration(definition)
                if declaration.key != entry.name:
                    raise ValueError("Connector key must match the entry-point name")
                pair = (definition, CatalogItem(key=entry.name, declaration=declaration, implementation=provenance))
            except Exception as exc:
                pair = (
                    None,
                    CatalogItem(
                        key=entry.name,
                        available=False,
                        diagnostic=f"Plugin declaration failed ({type(exc).__name__}).",
                        implementation=provenance,
                    ),
                )
            candidates.setdefault(entry.name, []).append(pair)
        self.definitions: dict[str, Definition] = {}
        self.items: dict[str, CatalogItem] = {}
        for key, values in candidates.items():
            if len(values) != 1:
                self.items[key] = CatalogItem(
                    key=key, available=False, diagnostic="Duplicate Connector key; remove the conflicting registration."
                )
            else:
                definition, item = values[0]
                self.items[key] = item
                if definition is not None:
                    self.definitions[key] = definition
