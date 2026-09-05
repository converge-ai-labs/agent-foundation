"""Transport validation for explicitly selected Organization collections."""

from a13n_service.public_errors import PublicError

from .authorization import AuthenticatedActor


def require_organization_boundary(actor: AuthenticatedActor, organization_id: str) -> None:
    if actor.boundary_workspace_id is not None or actor.boundary_organization_id != organization_id:
        raise PublicError("resource_not_found", "The requested resource was not found.", status_code=404)
