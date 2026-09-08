"""Finite role compatibility and structural checks, without grant mutation or I/O."""

from .domain import AuthorizationError
from .models import RoleBindingRecord

ROLE_KEYS = {
    ("organization", "user"): frozenset({"member", "admin"}),
    ("workspace", "user"): frozenset({"viewer", "runner", "builder", "admin"}),
    ("workspace", "service_account"): frozenset({"viewer", "runner", "builder"}),
    ("agent", "user"): frozenset({"viewer", "runner", "builder"}),
    ("agent", "service_account"): frozenset({"viewer", "runner", "builder"}),
}


def validate_binding(binding: RoleBindingRecord) -> None:
    if binding.role_key not in ROLE_KEYS.get((binding.resource_type, binding.principal_type), ()):
        raise AuthorizationError("invalid_role_binding")
    if binding.resource_type == "organization":
        valid = binding.workspace_id is None and binding.resource_id == binding.organization_id
    else:
        valid = binding.workspace_id is not None and (
            binding.resource_type == "agent" or binding.resource_id == binding.workspace_id
        )
    if not valid:
        raise AuthorizationError("invalid_role_binding")
