"""Public instance capabilities are derived from its frozen configuration."""

from a13n_service.runs.environments.connection import preview_support
from a13n_service.runs.environments.schemas import EnvironmentView, Handle
from a13n_service.runs.environments.tables import EnvironmentRow


def environment_view(row: EnvironmentRow) -> EnvironmentView:
    view = EnvironmentView.model_validate(row)
    handle = Handle.model_validate(row.handle) if row.handle is not None else None
    provider_type = (row.provider_identity or {}).get("type")
    view.desktop_preview = preview_support(handle, provider_type if isinstance(provider_type, str) else None)
    return view
